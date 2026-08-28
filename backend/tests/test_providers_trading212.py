from __future__ import annotations

import base64
from unittest.mock import patch

import httpx
import pytest

import app.providers as provider_registry
from app.providers import all_known_providers, get_provider
from app.providers.trading212 import Trading212Provider


def test_provider_is_registered_as_an_enabled_brokerage_token_connector():
    # The suite intentionally clears configured providers between tests; rerun
    # the bootstrap path to exercise actual application registration.
    provider_registry._auto_register_providers()
    provider = get_provider("trading212")

    assert isinstance(provider, Trading212Provider)
    assert provider.flow_type == "token"
    assert provider.kind == "brokerage"
    provider_entry = next(item for item in all_known_providers() if item["name"] == "trading212")
    assert provider_entry["configured"] is True
    assert provider_entry["supports_asset_sync"] is True


@pytest.mark.asyncio
async def test_token_callback_authenticates_against_the_selected_read_only_host():
    summary = {
        "id": 123456789,
        "currency": "EUR",
        "cash": {"availableToTrade": 0, "inPies": 0, "reservedForOrders": 0},
    }
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json=summary)

    transport = httpx.MockTransport(handler)

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport)

    with patch.object(Trading212Provider, "_client", fake_client):
        connection = await Trading212Provider().handle_oauth_callback(
            "demo:api-key-123:api-secret-456"
        )

    assert seen == {
        "host": "demo.trading212.com",
        "path": "/api/v0/equity/account/summary",
        "authorization": f"Basic {base64.b64encode(b'api-key-123:api-secret-456').decode()}",
    }
    assert connection.external_id == "123456789"
    assert connection.institution_name == "Trading 212"
    assert connection.credentials["environment"] == "demo"
    assert "api-key-123" not in str(connection.credentials)
    assert "api-secret-456" not in str(connection.credentials)
    assert [
        (account.external_id, account.name, account.type, account.balance, account.currency)
        for account in connection.accounts
    ] == [
        ("trading212:123456789:cash", "Trading 212 Cash", "investment", 0, "EUR")
    ]


@pytest.mark.asyncio
async def test_account_and_position_reads_normalize_balances_and_preserve_provider_metadata():
    summary = {
        "id": 123456789,
        "currency": "EUR",
        "cash": {"availableToTrade": 100.10, "inPies": 25.40, "reservedForOrders": 4.50},
        "investments": {"currentValue": 1000.00, "totalCost": 900.00},
        "totalValue": 1130.00,
    }
    positions = [
        {
            "ticker": "AAPL_US_EQ",
            "quantity": 2.5,
            "quantityInPies": 0.5,
            "currentPrice": 200.34,
            "instrument": {
                "ticker": "AAPL_US_EQ",
                "name": "Apple Inc.",
                "isin": "US0378331005",
                "currency": "USD",
            },
            "walletImpact": {"currency": "EUR", "currentValue": 501.00, "totalCost": 450.00},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/account/summary"):
            return httpx.Response(200, json=summary)
        if request.url.path.endswith("/positions"):
            return httpx.Response(200, json=positions)
        raise AssertionError(f"unexpected endpoint: {request.url.path}")

    transport = httpx.MockTransport(handler)

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport)

    credentials = {"api_key": "key", "api_secret": "secret", "environment": "demo"}
    with patch.object(Trading212Provider, "_client", fake_client):
        accounts = await Trading212Provider().get_accounts(credentials)
        holdings = await Trading212Provider().get_holdings(credentials)

    assert len(accounts) == 1
    assert accounts[0].external_id == "trading212:123456789:cash"
    assert accounts[0].balance == 130
    account_metadata = accounts[0].metadata
    assert account_metadata is not None
    assert account_metadata["trading212"]["cash"]["inPies"] == "25.4"
    assert account_metadata["trading212"]["totalValue"] == "1130.0"
    assert len(holdings) == 1
    assert holdings[0].external_id == "trading212:position:AAPL_US_EQ"
    assert holdings[0].current_value == 501
    assert holdings[0].purchase_price == 450
    assert holdings[0].ticker == "AAPL_US_EQ"
    holding_metadata = holdings[0].metadata
    assert holding_metadata is not None
    assert holding_metadata["trading212"]["quantityInPies"] == "0.5"


@pytest.mark.asyncio
async def test_empty_positions_response_is_a_valid_empty_portfolio():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/positions")
        return httpx.Response(200, json=[])

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with patch.object(Trading212Provider, "_client", fake_client):
        holdings = await Trading212Provider().get_holdings({"api_key": "key", "api_secret": "secret"})

    assert holdings == []


@pytest.mark.asyncio
async def test_malformed_positions_response_is_a_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/positions")
        return httpx.Response(200, json={"unexpected": "payload"})

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with patch.object(Trading212Provider, "_client", fake_client), pytest.raises(
        ValueError, match="unrecognized shape"
    ):
        await Trading212Provider().get_holdings({"api_key": "key", "api_secret": "secret"})


@pytest.mark.asyncio
async def test_transaction_history_recovers_from_a_stale_next_page_cursor_without_losing_rows():
    seen_queries: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_queries.append(request.url.query)
        if request.url.query == b"limit=50":
            return httpx.Response(
                200,
                json={
                    "items": [{"reference": "first", "dateTime": "2026-08-01T12:00:00Z"}],
                    "nextPagePath": "?cursor=stale",
                },
            )
        if request.url.query == b"cursor=stale":
            return httpx.Response(404, json={"type": "/api-errors/entity-not-found"})
        if request.url.query == b"limit=50&cursor=first&time=2026-08-01T12%3A00%3A00Z":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"reference": "first", "dateTime": "2026-08-01T12:00:00Z"},
                        {"reference": "second", "dateTime": "2026-07-31T12:00:00Z"},
                    ]
                },
            )
        raise AssertionError(f"unexpected query: {request.url.query}")

    transport = httpx.MockTransport(handler)

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=transport)

    with patch.object(Trading212Provider, "_client", fake_client):
        rows = await Trading212Provider().get_history_transactions(
            {"api_key": "key", "api_secret": "secret"}
        )

    assert [row["reference"] for row in rows] == ["first", "second"]
    assert any(query == b"cursor=stale" for query in seen_queries)


@pytest.mark.asyncio
async def test_history_transactions_and_dividends_become_idempotent_cash_transactions():
    provider = Trading212Provider()

    async def transactions(credentials, limit=50):  # noqa: ANN001
        return [{"reference": "deposit-1", "type": "DEPOSIT", "amount": 12.5, "dateTime": "2026-08-01"}]

    async def dividends(credentials, limit=50):  # noqa: ANN001
        return [{"reference": "div-1", "ticker": "AAPL_US_EQ", "amount": 0.7, "paidOn": "2026-08-02", "currency": "USD"}]

    with (
        patch.object(provider, "get_history_transactions", transactions),
        patch.object(provider, "get_dividends", dividends),
    ):
        rows = await provider.get_transactions({}, "trading212:1:cash")

    assert [(row.external_id, row.type, str(row.amount)) for row in rows] == [
        ("t212:cash:deposit-1", "credit", "12.5"),
        ("t212:dividend:div-1", "credit", "0.7"),
    ]


@pytest.mark.parametrize(
    ("kind", "amount", "expected_type", "ignored"),
    [
        ("DEPOSIT", "10", "credit", False),
        ("WITHDRAWAL", "10", "debit", False),
        ("FEE", "10", "debit", False),
        ("TRANSFER", "10", "credit", True),
        ("TRANSFER", "-10", "debit", True),
    ],
)
def test_cash_history_uses_documented_type_direction_not_amount_sign(
    kind, amount, expected_type, ignored
):
    row = Trading212Provider._map_history_transaction(
        {"reference": f"{kind}-{amount}", "type": kind, "amount": amount, "dateTime": "2026-08-01"}
    )

    assert row.type == expected_type
    assert row.is_ignored is ignored
    assert row.amount == 10


def test_unknown_or_malformed_cash_history_is_rejected_instead_of_inventing_a_transaction():
    with pytest.raises(ValueError):
        Trading212Provider._map_history_transaction(
            {"reference": "unknown", "type": "MYSTERY", "amount": "10", "dateTime": "2026-08-01"}
        )
    with pytest.raises(ValueError):
        Trading212Provider._map_history_transaction(
            {"reference": "bad-amount", "type": "DEPOSIT", "amount": "NaN", "dateTime": "2026-08-01"}
        )


def test_malformed_summary_and_unbounded_fallback_ids_fail_safely():
    with pytest.raises(ValueError):
        Trading212Provider._accounts_from_summary({"currency": "EUR", "cash": {}})
    row = Trading212Provider._map_history_transaction(
        {"type": "DEPOSIT", "amount": "1", "dateTime": "2026-08-01", "payload": "x" * 1000}
    )
    assert len(row.external_id) < 255
    assert row.external_id == Trading212Provider._map_history_transaction(
        {"type": "DEPOSIT", "amount": "1", "dateTime": "2026-08-01", "payload": "x" * 1000}
    ).external_id


@pytest.mark.parametrize("amount", [None, "not-a-number", "NaN", "Infinity"])
def test_dividend_rejects_missing_malformed_or_nonfinite_amounts(amount):
    with pytest.raises(ValueError, match="dividend amount"):
        Trading212Provider._map_dividend(
            {"reference": "dividend-1", "amount": amount, "paidOn": "2026-08-02"}
        )


def test_dividend_external_id_is_bounded_and_rejects_malformed_references():
    oversized = {"reference": "x" * 241, "amount": "0.70", "paidOn": "2026-08-02"}
    malformed = {"reference": {"nested": "id"}, "amount": "0.70", "paidOn": "2026-08-02"}

    with pytest.raises(ValueError, match="dividend reference"):
        Trading212Provider._map_dividend(oversized)
    with pytest.raises(ValueError, match="dividend reference"):
        Trading212Provider._map_dividend(malformed)
