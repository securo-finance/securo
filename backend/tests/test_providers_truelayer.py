"""Unit tests for the TrueLayer provider."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
import pytest

from app.agents.services.crypto import decrypt, encrypt
from app.providers.base import SessionExpiredError
from app.providers.truelayer import (
    TRUELAYER_SCOPES,
    TrueLayerProvider,
    _balance_amount,
    _credentials_from_token_payload,
    _https_endpoint,
    _to_decimal,
    _token_value,
)


@pytest.fixture
def truelayer_env(monkeypatch):
    monkeypatch.setenv("TRUELAYER_CLIENT_ID", "tl-client")
    monkeypatch.setenv("TRUELAYER_CLIENT_SECRET", "tl-secret")
    monkeypatch.setenv("TRUELAYER_AUTH_URL", "https://auth.truelayer.test")
    monkeypatch.setenv("TRUELAYER_API_URL", "https://api.truelayer.test/data/v1")
    monkeypatch.setenv("TRUELAYER_REDIRECT_URI", "https://app.example.com/oauth/callback")
    from app.agents.services import crypto
    from app.core.config import get_settings

    get_settings.cache_clear()
    crypto._fernet.cache_clear()
    yield
    get_settings.cache_clear()
    crypto._fernet.cache_clear()


def _patch_clients(provider: TrueLayerProvider, handler):
    transport = httpx.MockTransport(handler)

    def auth_client():
        return httpx.AsyncClient(
            base_url="https://auth.truelayer.test",
            transport=transport,
        )

    def api_client(access_token: str):
        return httpx.AsyncClient(
            base_url="https://api.truelayer.test/data/v1",
            transport=transport,
            headers={"Authorization": " ".join(("Bearer", access_token))},
        )

    return (
        patch.object(provider, "_auth_client", side_effect=auth_client),
        patch.object(provider, "_api_client", side_effect=api_client),
    )


@pytest.mark.asyncio
async def test_get_oauth_url_builds_authorization_url(truelayer_env):
    provider = TrueLayerProvider()

    url = await provider.get_oauth_url(
        "https://app.example.com/oauth/callback",
        "state-123",
        flow_params={"providers": "uk-oauth-all", "scope": "info accounts"},
    )

    assert url.startswith("https://auth.truelayer.test/?")
    query = parse_qs(url.split("?", 1)[1])
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["tl-client"]
    assert query["redirect_uri"] == ["https://app.example.com/oauth/callback"]
    assert query["scope"] == ["info accounts"]
    assert query["state"] == ["state-123"]
    assert query["providers"] == ["uk-oauth-all"]


@pytest.mark.asyncio
async def test_handle_oauth_callback_exchanges_token_and_maps_accounts(truelayer_env):
    provider = TrueLayerProvider()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/connect/token":
            body = parse_qs(request.read().decode())
            assert body["grant_type"] == ["authorization_code"]
            assert body["code"] == ["auth-code"]
            assert body["redirect_uri"] == ["https://app.example.com/oauth/callback"]
            assert body["client_id"] == ["tl-client"]
            assert body["client_secret"] == ["tl-secret"]
            return httpx.Response(
                200,
                json={
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        if request.url.path == "/data/v1/accounts":
            assert request.headers["authorization"] == " ".join(("Bearer", "access-1"))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "account_id": "acc-1",
                            "display_name": "Current account",
                            "account_type": "TRANSACTION",
                            "currency": "GBP",
                            "account_number": {"number": "12345678"},
                            "provider": {
                                "provider_id": "lloyds",
                                "display_name": "Lloyds Bank",
                                "logo_uri": "https://logos/lloyds.png",
                            },
                        }
                    ]
                },
            )
        if request.url.path == "/data/v1/accounts/acc-1/balance":
            return httpx.Response(
                200,
                json={"results": [{"currency": "GBP", "current": "123.45"}]},
            )
        if request.url.path == "/data/v1/cards":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404)

    auth_patch, api_patch = _patch_clients(provider, handler)
    with auth_patch, api_patch:
        conn = await provider.handle_oauth_callback("auth-code")

    assert [request.url.path for request in seen] == [
        "/connect/token",
        "/data/v1/accounts",
        "/data/v1/cards",
        "/data/v1/accounts/acc-1/balance",
    ]
    assert conn.external_id == "lloyds"
    assert conn.institution_name == "Lloyds Bank"
    assert conn.logo_url == "https://logos/lloyds.png"
    assert decrypt(conn.credentials["access_token_enc"]) == "access-1"
    assert decrypt(conn.credentials["refresh_token_enc"]) == "refresh-1"
    assert "access_token" not in conn.credentials
    assert "refresh_token" not in conn.credentials
    [account] = conn.accounts
    assert account.external_id == "acc-1"
    assert account.name == "Current account"
    assert account.type == "checking"
    assert account.balance == Decimal("123.45")
    assert account.masked_number == "5678"


@pytest.mark.asyncio
async def test_get_transactions_maps_signed_amounts_and_raw_data(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/data/v1/accounts/acc-1/transactions"
        assert request.url.params["from"] == "2026-01-01"
        assert request.url.params["to"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "transaction_id": "txn-1",
                        "timestamp": "2026-01-02T12:34:56Z",
                        "description": "Coffee",
                        "merchant_name": "Cafe",
                        "amount": "-4.50",
                        "currency": "GBP",
                        "transaction_category": "Eating out",
                    },
                    {
                        "transaction_id": "txn-2",
                        "timestamp": "2026-01-03T09:00:00Z",
                        "description": "Refund",
                        "amount": "2.00",
                        "currency": "GBP",
                        "status": "pending",
                    },
                ]
            },
        )

    with _patch_clients(provider, handler)[1]:
        txns = await provider.get_transactions(
            {"access_token": "access-1"}, "acc-1", since=date(2026, 1, 1)
        )

    assert len(txns) == 2
    assert txns[0].external_id == "txn-1"
    assert txns[0].amount == Decimal("4.50")
    assert txns[0].type == "debit"
    assert txns[0].date == date(2026, 1, 2)
    assert txns[0].payee == "Cafe"
    assert txns[0].raw_data is not None
    assert txns[0].raw_data["transaction_id"] == "txn-1"
    assert txns[1].amount == Decimal("2.00")
    assert txns[1].type == "credit"
    assert txns[1].status == "pending"


@pytest.mark.asyncio
async def test_refresh_credentials_uses_refresh_token(truelayer_env):
    provider = TrueLayerProvider()
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    credentials = {
        "access_token": "old-access",
        "refresh_token": "refresh-1",
        "expires_at": expired,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/connect/token"
        body = parse_qs(request.read().decode())
        assert body["grant_type"] == ["refresh_token"]
        assert body["refresh_token"] == ["refresh-1"]
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "expires_in": 7200,
                "token_type": "Bearer",
            },
        )

    with _patch_clients(provider, handler)[0]:
        refreshed = await provider.refresh_credentials(credentials)

    assert _token_value(refreshed, "access_token") == "new-access"
    assert _token_value(refreshed, "refresh_token") == "refresh-1"


@pytest.mark.asyncio
async def test_valid_credentials_do_not_refresh(truelayer_env):
    provider = TrueLayerProvider()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    credentials = {"access_token": "access-1", "expires_at": future}

    refreshed = await provider.refresh_credentials(credentials)

    assert refreshed is credentials


@pytest.mark.asyncio
async def test_api_401_signals_expired_session(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="expired")

    with _patch_clients(provider, handler)[1]:
        with pytest.raises(SessionExpiredError):
            await provider.get_transactions({"access_token": "bad"}, "acc-1")


@pytest.mark.asyncio
async def test_cards_are_keyed_by_account_id(truelayer_env):
    """TrueLayer's Cards API has no `card_id` — cards carry `account_id`."""
    provider = TrueLayerProvider()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/data/v1/accounts":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/data/v1/cards":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "account_id": "card-acc-1",
                            "display_name": "Visa Credit",
                            "card_type": "CREDIT",
                            "card_network": "VISA",
                            "currency": "GBP",
                            "partial_card_number": "4444",
                            "provider": {
                                "provider_id": "amex",
                                "display_name": "Amex",
                            },
                        }
                    ]
                },
            )
        if request.url.path == "/data/v1/cards/card-acc-1/balance":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "currency": "GBP",
                            "current": "250.00",
                            "credit_limit": "3000.00",
                            "payment_due": "25.00",
                            "payment_due_date": "2026-02-17T00:00:00Z",
                            "last_statement_balance": "250.00",
                            "last_statement_date": "2026-01-24T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(404, text="not found")

    with _patch_clients(provider, handler)[1]:
        accounts = await provider.get_accounts({"access_token": "access-1"})

    assert "/data/v1/cards/card-acc-1/balance" in seen
    [card] = accounts
    assert card.external_id == "card:card-acc-1"
    assert card.type == "credit_card"
    assert card.balance == Decimal("250.00")
    assert card.credit_limit == Decimal("3000.00")
    assert card.masked_number == "4444"
    assert card.minimum_payment == Decimal("25.00")
    assert card.payment_due_day == 17
    assert card.statement_close_day == 24
    assert card.card_brand == "VISA"


@pytest.mark.asyncio
async def test_card_transactions_use_the_card_endpoint(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/data/v1/cards/card-acc-1/transactions"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "transaction_id": "txn-1",
                        "timestamp": "2026-01-02T12:34:56Z",
                        "description": "Hotel",
                        "amount": "-99.00",
                        "currency": "GBP",
                    }
                ]
            },
        )

    with _patch_clients(provider, handler)[1]:
        txns = await provider.get_transactions(
            {"access_token": "access-1"}, "card:card-acc-1"
        )

    assert [txn.external_id for txn in txns] == ["txn-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 501])
async def test_providers_without_card_support_still_connect(truelayer_env, status_code):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/v1/accounts":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "account_id": "acc-1",
                            "display_name": "Current account",
                            "account_type": "TRANSACTION",
                            "currency": "GBP",
                        }
                    ]
                },
            )
        if request.url.path == "/data/v1/accounts/acc-1/balance":
            return httpx.Response(
                200, json={"results": [{"currency": "GBP", "current": "10.00"}]}
            )
        if request.url.path == "/data/v1/cards":
            return httpx.Response(status_code, text="endpoint_not_supported")
        raise AssertionError(f"unexpected path {request.url.path}")

    with _patch_clients(provider, handler)[1]:
        accounts = await provider.get_accounts({"access_token": "access-1"})

    assert [account.external_id for account in accounts] == ["acc-1"]


@pytest.mark.asyncio
async def test_api_error_message_includes_provider_body(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text='{"error":"internal_server_error"}')

    with _patch_clients(provider, handler)[1]:
        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await provider.get_accounts({"access_token": "access-1"})

    assert "internal_server_error" in str(excinfo.value)


@pytest.mark.asyncio
async def test_zero_current_balance_is_not_replaced_by_available(truelayer_env):
    """A paid-off card must read 0, not its whole credit limit."""
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/v1/accounts":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/data/v1/cards":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"account_id": "card-1", "display_name": "Visa", "currency": "GBP"}
                    ]
                },
            )
        if request.url.path == "/data/v1/cards/card-1/balance":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "currency": "GBP",
                            "current": 0,
                            "available": 3300,
                            "credit_limit": 3300,
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    with _patch_clients(provider, handler)[1]:
        [card] = await provider.get_accounts({"access_token": "access-1"})

    assert card.balance == Decimal("0")
    assert card.credit_limit == Decimal("3300")


@pytest.mark.asyncio
async def test_unreadable_balance_leaves_the_account_at_zero(truelayer_env):
    """A balance the bank will never serve must not fail the connection.

    Amex refuses supplementary-card balances for the life of the connection.
    """
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/v1/accounts":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "account_id": "acc-1",
                            "display_name": "Current account",
                            "account_type": "TRANSACTION",
                            "currency": "GBP",
                        }
                    ]
                },
            )
        if request.url.path == "/data/v1/accounts/acc-1/balance":
            return httpx.Response(404, text="account_not_found")
        if request.url.path == "/data/v1/cards":
            return httpx.Response(200, json={"results": []})
        raise AssertionError(f"unexpected path {request.url.path}")

    with _patch_clients(provider, handler)[1]:
        [account] = await provider.get_accounts({"access_token": "access-1"})

    assert account.external_id == "acc-1"
    assert account.balance == Decimal("0")
    assert account.currency == "GBP"


@pytest.mark.asyncio
async def test_card_metadata_is_omitted_when_the_provider_has_none(truelayer_env):
    """Partial payloads must leave the cycle fields unset, not zeroed.

    connection_service only overwrites stored CC metadata when the provider
    supplies a value, so None here preserves a user's manual entry.
    """
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/v1/accounts":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/data/v1/cards":
            return httpx.Response(
                200,
                json={"results": [{"account_id": "card-1", "display_name": "Card"}]},
            )
        if request.url.path == "/data/v1/cards/card-1/balance":
            return httpx.Response(
                200, json={"results": [{"currency": "GBP", "current": "10.00"}]}
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    with _patch_clients(provider, handler)[1]:
        [card] = await provider.get_accounts({"access_token": "access-1"})

    assert card.credit_limit is None
    assert card.minimum_payment is None
    assert card.payment_due_day is None
    assert card.statement_close_day is None
    assert card.card_brand is None
    assert card.card_level is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 501])
async def test_card_only_issuers_connect_without_an_accounts_endpoint(
    truelayer_env, status_code
):
    """Barclaycard/Amex expose no /accounts at all."""
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/v1/accounts":
            return httpx.Response(status_code, text="endpoint_not_supported")
        if request.url.path == "/data/v1/cards":
            return httpx.Response(
                200,
                json={"results": [{"account_id": "card-1", "display_name": "Amex"}]},
            )
        if request.url.path == "/data/v1/cards/card-1/balance":
            return httpx.Response(
                200, json={"results": [{"currency": "GBP", "current": "40.00"}]}
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    with _patch_clients(provider, handler)[1]:
        [card] = await provider.get_accounts({"access_token": "access-1"})

    assert card.external_id == "card:card-1"
    assert card.balance == Decimal("40.00")


@pytest.mark.asyncio
async def test_neither_endpoint_available_is_an_error_not_an_empty_bank(truelayer_env):
    """A wrong API base 404s both lists; that must not read as "no accounts"."""
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="endpoint_not_supported")

    with _patch_clients(provider, handler)[1]:
        with pytest.raises(RuntimeError, match="neither /accounts nor /cards"):
            await provider.get_accounts({"access_token": "access-1"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500, text="upstream_error"),
        httpx.ReadTimeout("timed out"),
    ],
    ids=["server_error", "timeout"],
)
async def test_transient_balance_failures_propagate(truelayer_env, failure):
    """Sync overwrites account.balance unconditionally.

    A transient failure must abort rather than persist a zero over a real
    balance, so only permanently-unreadable balances degrade.
    """
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/v1/accounts":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "account_id": "acc-1",
                            "display_name": "Current account",
                            "currency": "GBP",
                        }
                    ]
                },
            )
        if request.url.path == "/data/v1/cards":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/data/v1/accounts/acc-1/balance":
            if isinstance(failure, httpx.Response):
                return failure
            raise failure
        raise AssertionError(f"unexpected path {request.url.path}")

    with _patch_clients(provider, handler)[1]:
        with pytest.raises(httpx.HTTPError):
            await provider.get_accounts({"access_token": "access-1"})


@pytest.mark.asyncio
async def test_expired_session_during_balance_still_triggers_reauth(truelayer_env):
    """401 is never tolerated — the connection must be flagged for reconnect."""
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/v1/accounts":
            return httpx.Response(
                200,
                json={"results": [{"account_id": "acc-1", "display_name": "Account"}]},
            )
        if request.url.path == "/data/v1/cards":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(401, text="invalid_token")

    with _patch_clients(provider, handler)[1]:
        with pytest.raises(SessionExpiredError):
            await provider.get_accounts({"access_token": "access-1"})


@pytest.mark.asyncio
async def test_card_balance_never_falls_back_to_available_credit(truelayer_env):
    """`available` on a card is headroom, the inverse of what it owes."""
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/v1/accounts":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/data/v1/cards":
            return httpx.Response(
                200,
                json={"results": [{"account_id": "card-1", "display_name": "Card"}]},
            )
        if request.url.path == "/data/v1/cards/card-1/balance":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"currency": "GBP", "available": "3279.00", "credit_limit": "3300.00"}
                    ]
                },
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    with _patch_clients(provider, handler)[1]:
        [card] = await provider.get_accounts({"access_token": "access-1"})

    assert card.balance == Decimal("0")
    assert card.credit_limit == Decimal("3300.00")


@pytest.mark.asyncio
async def test_current_account_still_falls_back_to_available(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/v1/accounts":
            return httpx.Response(
                200,
                json={"results": [{"account_id": "acc-1", "display_name": "Account"}]},
            )
        if request.url.path == "/data/v1/cards":
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/data/v1/accounts/acc-1/balance":
            return httpx.Response(
                200, json={"results": [{"currency": "GBP", "available": "12.50"}]}
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    with _patch_clients(provider, handler)[1]:
        [account] = await provider.get_accounts({"access_token": "access-1"})

    assert account.balance == Decimal("12.50")


_PROVIDER_CATALOGUE = [
    {
        "provider_id": "ob-monzo",
        "display_name": "Monzo",
        "country": "uk",
        "logo_url": "https://assets.truelayer.test/monzo.svg",
        "scopes": ["info", "accounts", "balance", "transactions", "offline_access"],
    },
    {
        "provider_id": "ob-amex",
        "display_name": "American Express",
        "country": "uk",
        "logo_url": "https://assets.truelayer.test/amex.svg",
        "scopes": ["info", "cards", "balance", "transactions", "offline_access"],
    },
    {
        "provider_id": "ob-bnp",
        "display_name": "BNP Paribas",
        "country": "fr",
        "logo_url": None,
        "scopes": ["info", "accounts", "balance", "transactions", "offline_access"],
    },
]


@pytest.mark.asyncio
async def test_list_institutions_maps_catalogue_and_normalises_country(truelayer_env):
    provider = TrueLayerProvider()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/api/providers"
        return httpx.Response(200, json=_PROVIDER_CATALOGUE)

    with _patch_clients(provider, handler)[0]:
        result = await provider.list_institutions()

    # TrueLayer says "uk"; the picker speaks ISO 3166-1 alpha-2.
    assert result.countries == ["FR", "GB"]
    assert [(i.name, i.display_name, i.country) for i in result.institutions] == [
        ("ob-bnp", "BNP Paribas", "FR"),
        ("ob-amex", "American Express", "GB"),
        ("ob-monzo", "Monzo", "GB"),
    ]
    assert result.institutions[1].logo == "https://assets.truelayer.test/amex.svg"
    # Scoped to this deployment's client, and never filtered by `scopes` —
    # that would drop the card-only issuers.
    query = parse_qs(seen[0].url.query.decode())
    assert query["clientId"] == ["tl-client"]
    assert "scopes" not in query


@pytest.mark.asyncio
async def test_list_institutions_translates_country_filter(truelayer_env):
    provider = TrueLayerProvider()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=[p for p in _PROVIDER_CATALOGUE if p["country"] == "uk"]
        )

    with _patch_clients(provider, handler)[0]:
        result = await provider.list_institutions("GB")

    assert parse_qs(seen[0].url.query.decode())["country"] == ["uk"]
    assert {i.country for i in result.institutions} == {"GB"}


@pytest.mark.asyncio
async def test_list_institutions_skips_entries_without_provider_id(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"display_name": "Nameless", "country": "uk"}, *_PROVIDER_CATALOGUE],
        )

    with _patch_clients(provider, handler)[0]:
        result = await provider.list_institutions()

    assert len(result.institutions) == len(_PROVIDER_CATALOGUE)


@pytest.mark.parametrize(
    "value",
    ["NaN", "-NaN", "sNaN", "Infinity", "-Infinity", "inf", float("nan"), float("inf")],
)
def test_to_decimal_rejects_non_finite_values(value):
    assert _to_decimal(value) is None


@pytest.mark.parametrize("value", ["12.50", "-3", 0, "0"])
def test_to_decimal_keeps_finite_values(value):
    assert _to_decimal(value) == Decimal(str(value))


def test_balance_amount_falls_through_non_finite_current():
    # A NaN `current` is unreadable, so `available` still gets its turn.
    assert _balance_amount({"current": float("nan"), "available": "10.00"}) == Decimal(
        "10.00"
    )


def test_balance_amount_returns_zero_when_every_field_non_finite():
    assert _balance_amount({"current": "NaN", "available": "Infinity"}) == Decimal("0")


def test_build_transaction_skips_non_finite_amount(truelayer_env):
    provider = TrueLayerProvider()

    # Guards against a regression that raises: Decimal("NaN") < 0 raises
    # InvalidOperation, so a leaked NaN would abort the sync, not just this row.
    assert (
        provider._build_transaction(
            "acc-1",
            {"amount": float("nan"), "timestamp": "2026-01-05T00:00:00Z"},
            "merchant",
        )
        is None
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://auth.truelayer.test",
        "HTTP://auth.truelayer.test",
        "ftp://auth.truelayer.test",
        "auth.truelayer.test",
        "",
        None,
    ],
)
def test_https_endpoint_rejects_non_https(value):
    with pytest.raises(ValueError):
        _https_endpoint(value, "TRUELAYER_AUTH_URL")


def test_https_endpoint_preserves_normalisation():
    assert (
        _https_endpoint("  https://api.truelayer.test/data/v1/  ", "TRUELAYER_API_URL")
        == "https://api.truelayer.test/data/v1"
    )


@pytest.mark.asyncio
async def test_plaintext_auth_url_blocks_credential_exchange(truelayer_env, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("TRUELAYER_AUTH_URL", "http://auth.truelayer.test")
    get_settings.cache_clear()
    provider = TrueLayerProvider()

    # The client secret is posted to this host — refuse to build the client.
    with pytest.raises(ValueError, match="TRUELAYER_AUTH_URL"):
        provider._auth_client()
    with pytest.raises(ValueError, match="TRUELAYER_AUTH_URL"):
        await provider.get_oauth_url("https://app.example.com/oauth/callback", "s")


def test_plaintext_api_url_blocks_bearer_token(truelayer_env, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("TRUELAYER_API_URL", "http://api.truelayer.test/data/v1")
    get_settings.cache_clear()
    provider = TrueLayerProvider()

    with pytest.raises(ValueError, match="TRUELAYER_API_URL"):
        provider._api_client("access-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[{"access_token": "a"}], "a-string", 42])
async def test_exchange_token_rejects_non_object_payload(truelayer_env, body):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with _patch_clients(provider, handler)[0]:
        # Would otherwise reach _credentials_from_token_payload and die on .get().
        with pytest.raises(httpx.HTTPStatusError, match="non-object payload"):
            await provider._exchange_token({"grant_type": "refresh_token"})


@pytest.mark.asyncio
async def test_exchange_token_rejects_non_json_body(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    with _patch_clients(provider, handler)[0]:
        with pytest.raises(httpx.HTTPStatusError, match="non-object payload"):
            await provider._exchange_token({"grant_type": "refresh_token"})


@pytest.mark.asyncio
async def test_data_api_rejects_non_object_payload(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"account_id": "acc-1"}])

    with _patch_clients(provider, handler)[1]:
        with pytest.raises(httpx.HTTPStatusError, match="non-object payload"):
            await provider._request(
                {"access_token": "access-1"}, "GET", "/accounts"
            )


@pytest.mark.asyncio
async def test_data_api_still_returns_mapping_payloads(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"account_id": "acc-1"}]})

    with _patch_clients(provider, handler)[1]:
        payload = await provider._request(
            {"access_token": "access-1"}, "GET", "/accounts"
        )

    assert payload == {"results": [{"account_id": "acc-1"}]}


@pytest.mark.asyncio
async def test_tolerated_status_still_returns_none_not_an_error(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "endpoint_not_supported"})

    with _patch_clients(provider, handler)[1]:
        # An absent resource is not a malformed one — it must stay None.
        assert (
            await provider._request(
                {"access_token": "access-1"}, "GET", "/cards", tolerate=(404,)
            )
            is None
        )


@pytest.mark.asyncio
async def test_provider_catalogue_still_accepts_a_json_array(truelayer_env):
    """The array guard must not have been applied to the public catalogue."""
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_PROVIDER_CATALOGUE)

    with _patch_clients(provider, handler)[0]:
        result = await provider.list_institutions()

    assert len(result.institutions) == len(_PROVIDER_CATALOGUE)


@pytest.mark.asyncio
async def test_refresh_credentials_keeps_scope_when_response_omits_it(truelayer_env):
    """A refresh grant need not echo `scope`, and None must not overwrite it.

    dict.update() in refresh_credentials merges the new payload over the stored
    credentials, so an unconditional `scope: None` would erase what the bank
    reported it granted at authorization.
    """
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/connect/token"
        return httpx.Response(
            200,
            # No "scope" key — the common shape for a refresh_token grant.
            json={"access_token": "access-2", "expires_in": 3600},
        )

    with _patch_clients(provider, handler)[0]:
        refreshed = await provider.refresh_credentials(
            {
                "access_token_enc": encrypt("access-1"),
                "refresh_token_enc": encrypt("refresh-1"),
                "expires_at": "2020-01-01T00:00:00+00:00",
                "scope": "accounts balance transactions offline_access",
            }
        )

    assert refreshed["scope"] == "accounts balance transactions offline_access"


@pytest.mark.asyncio
async def test_refresh_credentials_takes_a_reported_scope(truelayer_env):
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "access-2",
                "expires_in": 3600,
                "scope": "info accounts offline_access",
            },
        )

    with _patch_clients(provider, handler)[0]:
        refreshed = await provider.refresh_credentials(
            {
                "access_token_enc": encrypt("access-1"),
                "refresh_token_enc": encrypt("refresh-1"),
                "expires_at": "2020-01-01T00:00:00+00:00",
                "scope": "accounts balance transactions offline_access",
            }
        )

    assert refreshed["scope"] == "info accounts offline_access"


def test_credentials_from_token_payload_omits_an_unreported_scope():
    credentials = _credentials_from_token_payload(
        {"access_token": "a", "expires_in": 60}
    )
    # Absent, not None — so dict.update() cannot erase an earlier value.
    assert "scope" not in credentials


@pytest.mark.asyncio
async def test_get_accounts_still_expires_on_401(truelayer_env):
    # A revoked or dead token is a 401, and that must stay fatal.
    provider = TrueLayerProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_token"})

    with _patch_clients(provider, handler)[1]:
        with pytest.raises(SessionExpiredError):
            await provider.get_accounts({"access_token": "access-1"})




@pytest.mark.asyncio
async def test_get_oauth_url_always_requests_the_full_scope(truelayer_env):
    """The picked bank filters the link; it never narrows the scope.

    Tailoring the scope per provider was tried and removed: every endpoint
    gated by a scope then answers 403 for a healthy connection, which reads
    as a dead session and expires it. Requesting everything keeps the token
    uniform, and a bank that cannot serve a scope simply returns 404/501 on
    that resource, which get_accounts already tolerates.
    """
    provider = TrueLayerProvider()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=[])

    with _patch_clients(provider, handler)[0]:
        url = await provider.get_oauth_url(
            "https://app.example.com/oauth/callback",
            "state-123",
            flow_params={
                "institution_name": "ob-monzo",
                "country": "GB",
                "valid_until_days": None,
            },
        )

    query = parse_qs(url.split("?", 1)[1])
    assert query["providers"] == ["ob-monzo"]
    assert query["scope"] == [TRUELAYER_SCOPES]
    # And no catalogue lookup: building the link makes no network call at all.
    assert seen == []


def test_requested_scopes_are_all_used_and_exclude_identity():
    """Least privilege: every requested scope must back a real endpoint call.

    `info` in particular returns the account holder's name, date of birth,
    address, phone and email. Nothing in this provider reads /info, so asking
    for it would surface identity data on the consent screen of every bank a
    user connects, for no functional gain.
    """
    requested = set(TRUELAYER_SCOPES.split())

    assert requested == {
        "accounts",  # GET /accounts
        "balance",  # GET /{accounts,cards}/{id}/balance
        "transactions",  # GET /{accounts,cards}/{id}/transactions
        "cards",  # GET /cards
        "offline_access",  # refresh token
    }
    assert "info" not in requested


@pytest.mark.asyncio
async def test_authorization_url_requests_no_identity_scope(truelayer_env):
    provider = TrueLayerProvider()

    url = await provider.get_oauth_url(
        "https://app.example.com/oauth/callback", "state-123"
    )

    scope = parse_qs(url.split("?", 1)[1])["scope"][0].split()
    assert "info" not in scope
    assert "offline_access" in scope
