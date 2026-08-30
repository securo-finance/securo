"""Unit tests for the Akahu provider.

The Akahu API is fully fakeable via ``httpx.MockTransport`` — no Akahu
credentials needed, no network. Each test stands up the smallest payload
required and asserts the parse / dispatch behavior we care about.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from app.providers.base import (
    ProviderRateLimited,
    ProviderUserActionRequired,
    SessionExpiredError,
)
from app.providers.akahu import AkahuProvider, _iso_to_date, _parse_tokens

APP_TOKEN = "app_token_ck1234567890abcdef"
USER_TOKEN = "user_token_ck0987654321fedcba"
# Plaintext credentials exercise the decrypt-fallback path, same as the
# SimpleFIN tests do with ``access_url``.
CREDS = {"app_token": APP_TOKEN, "user_token": USER_TOKEN}


def _patched_client(handler):
    """Replace AkahuProvider._client with one wired to a MockTransport."""

    transport = httpx.MockTransport(handler)

    async def fake_client(self):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport, timeout=30)

    return patch.object(AkahuProvider, "_client", fake_client)


# ----- token parsing ----------------------------------------------------------


def test_parse_tokens_any_order_and_whitespace():
    blob = f"  {USER_TOKEN}\n{APP_TOKEN}  "
    assert _parse_tokens(blob) == (APP_TOKEN, USER_TOKEN)


def test_parse_tokens_strips_punctuation():
    blob = f'"{APP_TOKEN}", {USER_TOKEN};'
    assert _parse_tokens(blob) == (APP_TOKEN, USER_TOKEN)


def test_parse_tokens_rejects_missing_user_token():
    with pytest.raises(ValueError):
        _parse_tokens(APP_TOKEN)


def test_parse_tokens_rejects_empty():
    with pytest.raises(ValueError):
        _parse_tokens("   ")


def test_iso_to_date():
    assert _iso_to_date("2026-08-20T02:15:00Z") == date(2026, 8, 20)
    assert _iso_to_date("2026-08-27T12:00:00Z") == date(2026, 8, 28)
    assert _iso_to_date("2026-08-29T06:22:00Z") == date(2026, 8, 29)
    assert _iso_to_date("2026-08-29T12:00:00Z") == date(2026, 8, 30)
    assert _iso_to_date("2026-08-30") == date(2026, 8, 30)
    assert _iso_to_date(None) is None
    assert _iso_to_date("nope") is None


# ----- accounts ---------------------------------------------------------------


def _account_item(**overrides):
    item = {
        "_id": "acc_123",
        "name": "Every day",
        "type": "CHECKING",
        "formatted_account": "38-9000-0123456-00",
        "balance": {"current": 108.66, "currency": "NZD"},
        "connection": {"name": "Kiwibank", "logo": "https://cdn.akahu.nz/kiwibank.png"},
    }
    item.update(overrides)
    return item


@pytest.mark.asyncio
async def test_get_accounts_maps_fields():
    def handler(request):
        assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
        assert request.headers["X-Akahu-Id"] == APP_TOKEN
        return httpx.Response(200, json={"success": True, "items": [_account_item()]})

    with _patched_client(handler):
        accounts = await AkahuProvider().get_accounts(CREDS)

    assert len(accounts) == 1
    acc = accounts[0]
    assert acc.external_id == "acc_123"
    assert acc.name == "Every day"
    assert acc.type == "checking"
    assert acc.balance == Decimal("108.66")
    assert acc.currency == "NZD"
    assert acc.masked_number == "5600"


@pytest.mark.asyncio
async def test_credit_card_balance_stored_positive_for_debt():
    item = _account_item(
        _id="acc_cc",
        type="CREDITCARD",
        balance={"current": -1775.81, "currency": "NZD", "limit": 5000},
    )

    def handler(request):
        return httpx.Response(200, json={"success": True, "items": [item]})

    with _patched_client(handler):
        accounts = await AkahuProvider().get_accounts(CREDS)

    assert accounts[0].type == "credit_card"
    # Akahu reports debt as negative; the sync layer expects positive-for-debt.
    assert accounts[0].balance == Decimal("1775.81")
    assert accounts[0].credit_limit == Decimal("5000")


@pytest.mark.asyncio
async def test_unknown_account_type_falls_back_to_checking():
    item = _account_item(_id="acc_tax", type="TAX", balance={"current": 12.34, "currency": "NZD"})

    def handler(request):
        return httpx.Response(200, json={"success": True, "items": [item]})

    with _patched_client(handler):
        accounts = await AkahuProvider().get_accounts(CREDS)

    assert accounts[0].type == "checking"
    assert accounts[0].balance == Decimal("12.34")


# ----- connection flow --------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_oauth_callback_builds_connection():
    def handler(request):
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"success": True, "item": {"_id": "user_abc"}})
        return httpx.Response(200, json={"success": True, "items": [_account_item()]})

    with _patched_client(handler):
        conn = await AkahuProvider().handle_oauth_callback(f"{APP_TOKEN}\n{USER_TOKEN}")

    assert conn.external_id == "user_abc"
    assert conn.institution_name == "Kiwibank"
    assert conn.logo_url == "https://cdn.akahu.nz/kiwibank.png"
    assert len(conn.accounts) == 1
    # Tokens are stored encrypted, never in plaintext keys.
    assert "app_token" not in conn.credentials
    assert conn.credentials["app_token_enc"]
    assert conn.credentials["user_token_enc"]


@pytest.mark.asyncio
async def test_multiple_banks_fall_back_to_akahu_umbrella_name():
    items = [
        _account_item(),
        _account_item(_id="acc_2", connection={"name": "ANZ", "logo": None}),
    ]

    def handler(request):
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"success": True, "item": {"_id": "user_abc"}})
        return httpx.Response(200, json={"success": True, "items": items})

    with _patched_client(handler):
        conn = await AkahuProvider().handle_oauth_callback(f"{APP_TOKEN} {USER_TOKEN}")

    assert conn.institution_name == "Akahu"


# ----- transactions -----------------------------------------------------------


def _txn_item(**overrides):
    item = {
        "_id": "trans_1",
        "_account": "acc_123",
        "date": "2026-08-18T00:00:00Z",
        "description": "WOOLWORTHS NZ/2 AVERILLPAPAKURA",
        "amount": -19.81,
        "type": "EFTPOS",
        "merchant": {"name": "Woolworths"},
    }
    item.update(overrides)
    return item


@pytest.mark.asyncio
async def test_get_transactions_paginates_and_maps():
    pages = [
        {"success": True, "items": [_txn_item()], "cursor": {"next": "page2"}},
        {
            "success": True,
            "items": [
                _txn_item(_id="trans_2", amount=4474.90, description="Payroll", merchant=None)
            ],
            "cursor": {"next": None},
        },
    ]
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=pages[len(calls) - 1])

    with _patched_client(handler):
        txns = await AkahuProvider().get_transactions(CREDS, "acc_123", since=date(2025, 8, 21))

    assert len(calls) == 2
    assert "cursor" not in calls[0] and calls[1]["cursor"] == "page2"
    assert calls[0]["start"].startswith("2025-08-21")

    debit, credit = txns
    assert debit.external_id == "trans_1"
    assert debit.type == "debit"
    assert debit.amount == Decimal("19.81")
    assert debit.date == date(2026, 8, 18)
    assert debit.payee == "Woolworths"
    assert credit.type == "credit"
    assert credit.amount == Decimal("4474.90")
    assert credit.payee is None


@pytest.mark.asyncio
async def test_transactions_skip_items_without_id():
    def handler(request):
        return httpx.Response(
            200,
            json={"success": True, "items": [_txn_item(_id=""), _txn_item(_id="trans_ok")]},
        )

    with _patched_client(handler):
        txns = await AkahuProvider().get_transactions(CREDS, "acc_123", since=date(2026, 1, 1))

    assert [t.external_id for t in txns] == ["trans_ok"]


# ----- errors / refresh -------------------------------------------------------


@pytest.mark.asyncio
async def test_401_raises_user_action_required():
    def handler(request):
        return httpx.Response(401, json={"success": False, "message": "Unauthorized"})

    with _patched_client(handler):
        with pytest.raises(ProviderUserActionRequired) as exc:
            await AkahuProvider().get_accounts(CREDS)
    assert exc.value.code == "credentials_invalid"


@pytest.mark.asyncio
async def test_429_raises_rate_limited():
    def handler(request):
        return httpx.Response(429, json={"success": False})

    with _patched_client(handler):
        with pytest.raises(ProviderRateLimited):
            await AkahuProvider().get_accounts(CREDS)


@pytest.mark.asyncio
async def test_refresh_credentials_requires_tokens():
    with pytest.raises(SessionExpiredError):
        await AkahuProvider().refresh_credentials({})


@pytest.mark.asyncio
async def test_trigger_refresh_posts_and_reports():
    def handler(request):
        assert request.method == "POST"
        assert request.url.path.endswith("/refresh")
        return httpx.Response(200, json={"success": True})

    with _patched_client(handler):
        assert await AkahuProvider().trigger_refresh(CREDS) == "refreshed"


@pytest.mark.asyncio
async def test_trigger_refresh_rate_limit_is_transient_failure():
    def handler(request):
        return httpx.Response(429, json={"success": False})

    with _patched_client(handler):
        assert await AkahuProvider().trigger_refresh(CREDS) == "failed"
