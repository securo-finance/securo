"""Unit tests for the Fintoc provider.

Fintoc is fully fakeable via httpx.MockTransport — no Fintoc credentials needed,
no network. Each test stands up the smallest payload required and asserts the
parse / dispatch behavior we care about.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from app.providers.base import ProviderRateLimited, SessionExpiredError
from app.providers.fintoc import (
    FintocProvider,
    _build_account_data,
    _build_transaction_data,
    _map_account_type,
)


def _patched_client(handler):
    """Replace FintocProvider._get_headers and wire httpx to MockTransport."""
    transport = httpx.MockTransport(handler)

    original_init = httpx.AsyncClient.__init__

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_init(*args, **kwargs)

    return patch.object(httpx.AsyncClient, "__init__", fake_async_client)


# ---- pure helpers -----------------------------------------------------------


def test_map_account_type_checking():
    assert _map_account_type("checking_account") == "checking"


def test_map_account_type_savings():
    assert _map_account_type("savings_account") == "savings"


def test_map_account_type_vista_is_checking():
    assert _map_account_type("vista_account") == "checking"


def test_map_account_type_unknown_defaults_to_checking():
    assert _map_account_type("mystery_account") == "checking"


def test_build_account_data_uses_available_balance():
    acc = {
        "id": "acc-1",
        "name": "Cuenta Corriente",
        "official_name": "Cuenta Corriente BCI",
        "type": "checking_account",
        "currency": "CLP",
        "balance": {"available": 500000, "current": 550000},
    }
    result = _build_account_data(acc)
    assert result.external_id == "acc-1"
    assert result.name == "Cuenta Corriente BCI"
    assert result.type == "checking"
    assert result.balance == Decimal("500000")
    assert result.currency == "CLP"


def test_build_account_data_falls_back_to_current_balance():
    acc = {
        "id": "acc-2",
        "name": "Ahorro",
        "type": "savings_account",
        "currency": "CLP",
        "balance": {"current": 200000},
    }
    result = _build_account_data(acc)
    assert result.balance == Decimal("200000")


def test_build_transaction_data_charge_is_debit():
    mov = {
        "id": "mov-1",
        "amount": 15000,
        "post_date": "2026-06-01",
        "description": "Supermercado Jumbo",
        "type": "charge",
        "currency": "CLP",
    }
    result = _build_transaction_data(mov)
    assert result.external_id == "mov-1"
    assert result.type == "debit"
    assert result.amount == Decimal("15000")
    assert result.currency == "CLP"
    assert result.date == date(2026, 6, 1)
    assert result.status == "posted"


def test_build_transaction_data_deposit_is_credit():
    mov = {
        "id": "mov-2",
        "amount": 1000000,
        "post_date": "2026-06-10",
        "description": "Sueldo",
        "type": "deposit",
        "currency": "CLP",
    }
    result = _build_transaction_data(mov)
    assert result.type == "credit"
    assert result.amount == Decimal("1000000")


def test_build_transaction_data_clp_no_scaling():
    """CLP is a whole-unit currency — Fintoc returns integers, no centavo scaling."""
    mov = {"id": "m", "amount": 1234, "post_date": "2026-01-01", "type": "charge"}
    result = _build_transaction_data(mov)
    assert result.amount == Decimal("1234")


def test_build_transaction_data_falls_back_to_transaction_date():
    mov = {"id": "m2", "amount": 500, "transaction_date": "2026-05-15", "type": "deposit"}
    result = _build_transaction_data(mov)
    assert result.date == date(2026, 5, 15)


# ---- provider metadata ------------------------------------------------------


def test_name_is_fintoc():
    assert FintocProvider().name == "fintoc"


def test_flow_type_is_widget():
    assert FintocProvider().flow_type == "widget"


@pytest.mark.asyncio
async def test_get_oauth_url_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        await FintocProvider().get_oauth_url("https://x", "state")


# ---- create_connect_token ---------------------------------------------------


@pytest.mark.asyncio
async def test_create_connect_token_success():
    """create_connect_token calls /v1/link_intents and returns the widget_token."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/link_intents" in str(request.url)
        body = request.read().decode("utf-8")
        assert "movements" in body
        return httpx.Response(
            201,
            json={
                "id": "li_12345",
                "widget_token": "widget_tok_abc123",
            },
        )

    provider = FintocProvider()
    with _patched_client(handler):
        result = await provider.create_connect_token("user-1")
    assert result.access_token == "widget_tok_abc123"


# ---- handle_oauth_callback --------------------------------------------------


@pytest.mark.asyncio
async def test_handle_oauth_callback_success():
    """exchange_token -> GET /links/exchange -> ConnectionData with accounts."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "/links/exchange" in str(request.url)
        assert request.url.params.get("exchange_token") == "lt_test_link_token"
        return httpx.Response(
            200,
            json={
                "id": "link_123",
                "link_token": "lt_test_link_token_perm",
                "institution": {"name": "BancoEstado"},
                "accounts": [
                    {
                        "id": "acc-cl-1",
                        "name": "Cuenta Vista",
                        "official_name": "Cuenta Vista BancoEstado",
                        "type": "vista_account",
                        "currency": "CLP",
                        "balance": {"available": 350000, "current": 350000},
                    }
                ],
            },
        )

    provider = FintocProvider()
    with _patched_client(handler):
        conn = await provider.handle_oauth_callback("lt_test_link_token")

    assert conn.external_id == "link_123"
    assert conn.institution_name == "BancoEstado"
    assert conn.credentials["link_token"] == "lt_test_link_token_perm"
    assert len(conn.accounts) == 1
    acc = conn.accounts[0]
    assert acc.external_id == "acc-cl-1"
    assert acc.type == "checking"
    assert acc.balance == Decimal("350000")


@pytest.mark.asyncio
async def test_handle_oauth_callback_fallback_institution_name():
    """Falls back to 'Chilean Bank' when institution field is absent."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "/links/exchange" in str(request.url)
        return httpx.Response(
            200,
            json={
                "id": "link_123",
                "link_token": "lt_perm",
                "accounts": [
                    {
                        "id": "acc-1",
                        "name": "Cuenta",
                        "type": "checking_account",
                        "currency": "CLP",
                        "balance": {"available": 1000},
                    }
                ],
            },
        )

    provider = FintocProvider()
    with _patched_client(handler):
        conn = await provider.handle_oauth_callback("lt_no_institution")

    assert conn.institution_name == "Chilean Bank"


@pytest.mark.asyncio
async def test_handle_oauth_callback_invalid_token_raises_session_expired():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid token"})

    provider = FintocProvider()
    with _patched_client(handler):
        with pytest.raises(SessionExpiredError):
            await provider.handle_oauth_callback("lt_bad_token")


# ---- get_accounts -----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_accounts_maps_types_correctly():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "a1",
                    "name": "Corriente",
                    "type": "checking_account",
                    "currency": "CLP",
                    "balance": {"available": 100},
                },
                {
                    "id": "a2",
                    "name": "Ahorro",
                    "type": "savings_account",
                    "currency": "CLP",
                    "balance": {"current": 200},
                },
                {
                    "id": "a3",
                    "name": "RUT",
                    "type": "vista_account",
                    "currency": "CLP",
                    "balance": {"available": 300},
                },
            ],
        )

    creds = {"link_token": "lt_test"}
    provider = FintocProvider()
    with _patched_client(handler):
        accounts = await provider.get_accounts(creds)

    by_id = {a.external_id: a for a in accounts}
    assert by_id["a1"].type == "checking"
    assert by_id["a2"].type == "savings"
    assert by_id["a3"].type == "checking"


# ---- get_transactions -------------------------------------------------------


@pytest.mark.asyncio
async def test_get_transactions_charge_is_debit_deposit_is_credit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "t1", "amount": 5000, "post_date": "2026-05-01",
                 "description": "Compra", "type": "charge", "currency": "CLP"},
                {"id": "t2", "amount": 100000, "post_date": "2026-05-10",
                 "description": "Transferencia recibida", "type": "deposit", "currency": "CLP"},
            ],
        )

    creds = {"link_token": "lt_test"}
    provider = FintocProvider()
    with _patched_client(handler):
        txns = await provider.get_transactions(creds, "acc-cl-1", since=date(2026, 5, 1))

    by_id = {t.external_id: t for t in txns}
    assert by_id["t1"].type == "debit"
    assert by_id["t1"].amount == Decimal("5000")
    assert by_id["t2"].type == "credit"
    assert by_id["t2"].amount == Decimal("100000")


@pytest.mark.asyncio
async def test_get_transactions_pagination_follows_next_cursor():
    """Fintoc cursor pagination: keep fetching until next_cursor is null."""
    pages = [
        {
            "data": [{"id": "t1", "amount": 1000, "post_date": "2026-01-01",
                      "type": "charge", "currency": "CLP"}],
            "next_cursor": "cursor_page2",
        },
        {
            "data": [{"id": "t2", "amount": 2000, "post_date": "2026-01-02",
                      "type": "deposit", "currency": "CLP"}],
            "next_cursor": None,
        },
    ]
    page_iter = iter(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(page_iter))

    creds = {"link_token": "lt_test"}
    provider = FintocProvider()
    with _patched_client(handler):
        txns = await provider.get_transactions(creds, "acc-cl-1")

    assert {t.external_id for t in txns} == {"t1", "t2"}


@pytest.mark.asyncio
async def test_get_transactions_uses_since_param():
    """The since date must be forwarded to Fintoc as an ISO date string."""
    received_since: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_since.append(request.url.params.get("since", ""))
        return httpx.Response(200, json=[])

    creds = {"link_token": "lt_test"}
    provider = FintocProvider()
    with _patched_client(handler):
        await provider.get_transactions(creds, "acc-cl-1", since=date(2026, 4, 15))

    assert received_since[0] == "2026-04-15"


@pytest.mark.asyncio
async def test_get_transactions_defaults_to_90_days():
    """Without a `since` date, fall back to 90 days ago."""
    received_since: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_since.append(request.url.params.get("since", ""))
        return httpx.Response(200, json=[])

    creds = {"link_token": "lt_test"}
    provider = FintocProvider()
    with _patched_client(handler):
        await provider.get_transactions(creds, "acc-cl-1")

    expected = (date.today() - timedelta(days=90)).isoformat()
    assert received_since[0] == expected


# ---- refresh_credentials ----------------------------------------------------


def test_refresh_credentials_returns_unchanged():
    """Fintoc link tokens have no expiry timer — refresh is a no-op."""
    creds = {"link_token": "lt_abc"}
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        FintocProvider().refresh_credentials(creds)
    )
    assert result == creds


# ---- list_institutions ------------------------------------------------------


@pytest.mark.asyncio
async def test_list_institutions_returns_cl_banks():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("country") == "cl"
        return httpx.Response(
            200,
            json=[
                {"id": "banco_de_chile", "name": "Banco de Chile", "country": "cl"},
                {"id": "santander", "name": "Banco Santander", "country": "cl",
                 "logo_url": "https://cdn.fintoc.com/santander.png"},
            ],
        )

    provider = FintocProvider()
    with _patched_client(handler):
        result = await provider.list_institutions(country="cl")

    assert result.countries == ["cl"]
    assert len(result.institutions) == 2
    bdc = next(i for i in result.institutions if i.name == "banco_de_chile")
    assert bdc.display_name == "Banco de Chile"
    santander = next(i for i in result.institutions if i.name == "santander")
    assert santander.logo == "https://cdn.fintoc.com/santander.png"


# ---- error mapping ----------------------------------------------------------


@pytest.mark.asyncio
async def test_401_raises_session_expired_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    creds = {"link_token": "lt_test"}
    provider = FintocProvider()
    with _patched_client(handler):
        with pytest.raises(SessionExpiredError):
            await provider.get_accounts(creds)


@pytest.mark.asyncio
async def test_429_raises_provider_rate_limited():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    creds = {"link_token": "lt_test"}
    provider = FintocProvider()
    with _patched_client(handler):
        with pytest.raises(ProviderRateLimited):
            await provider.get_accounts(creds)
