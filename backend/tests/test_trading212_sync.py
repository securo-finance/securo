from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.models.user import User
from app.providers.trading212 import Trading212Provider
from app.services.connection_service import (
    _sync_holdings,
    _sync_trading212_orders,
    handle_oauth_callback,
)


class _OrdersProvider:
    async def get_historical_orders(self, credentials: dict):
        return [
            {
                "order": {
                    "ticker": "AAPL_US_EQ",
                    "side": "BUY",
                    "currency": "USD",
                    "instrument": {"name": "Apple Inc.", "isin": "US0378331005"},
                },
                "fill": {
                    "id": "fill-1",
                    "type": "TRADE",
                    "quantity": "2",
                    "price": "100",
                    "filledAt": "2026-08-01T10:00:00Z",
                    "walletImpact": {"netValue": "-201", "currency": "USD", "taxes": [{"quantity": "1"}]},
                },
            }
        ]


@pytest.mark.asyncio
async def test_order_sync_upserts_asset_ledger_and_an_ignored_cash_settlement(
    session: AsyncSession, test_user: User
):
    connection = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        provider="trading212",
        kind="brokerage",
        external_id="123",
        institution_name="Trading 212",
        credentials={},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(connection)
    await session.flush()
    cash = Account(
        user_id=test_user.id,
        connection_id=connection.id,
        external_id="trading212:123:cash",
        name="Trading 212 Cash",
        type="investment",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(cash)
    await session.commit()

    await _sync_trading212_orders(session, test_user.id, connection, _OrdersProvider(), {})
    await session.commit()

    ledger = (await session.execute(select(AssetTransaction))).scalars().all()
    settlements = (await session.execute(select(Transaction))).scalars().all()
    assert [(row.external_id, row.kind, row.quantity, row.price, row.fee) for row in ledger] == [
        ("t212:fill:fill-1", "buy", Decimal("2"), Decimal("100"), Decimal("1"))
    ]
    assert [(row.external_id, row.is_ignored, row.type, row.amount) for row in settlements] == [
        ("t212:settlement:fill-1", True, "debit", Decimal("201"))
    ]


@pytest.mark.asyncio
async def test_fresh_t212_callback_creates_cash_account_and_initial_order_settlement(
    session: AsyncSession, test_user: User, test_workspace
):
    """A new token connection must create the cash account before order sync."""
    summary = {
        "id": 123456789,
        "currency": "USD",
        "cash": {"availableToTrade": 0, "inPies": 0, "reservedForOrders": 0},
    }
    positions = [
        {
            "ticker": "AAPL_US_EQ",
            "quantity": "2",
            "currentPrice": "100",
            "instrument": {"name": "Apple Inc.", "isin": "US0378331005"},
            "walletImpact": {"currency": "USD", "currentValue": "200", "totalCost": "201"},
        }
    ]
    orders = [
        {
            "order": {
                "ticker": "AAPL_US_EQ",
                "side": "BUY",
                "currency": "USD",
                "instrument": {"name": "Apple Inc.", "isin": "US0378331005"},
            },
            "fill": {
                "id": "fill-on-connect",
                "type": "TRADE",
                "quantity": "2",
                "price": "100",
                "filledAt": "2026-08-01T10:00:00Z",
                "walletImpact": {"netValue": "-201", "currency": "USD"},
            },
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/account/summary"):
            return httpx.Response(200, json=summary)
        if request.url.path.endswith("/positions"):
            return httpx.Response(200, json=positions)
        if request.url.path.endswith("/history/orders"):
            return httpx.Response(200, json=orders)
        if request.url.path.endswith("/history/transactions") or request.url.path.endswith(
            "/history/dividends"
        ):
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected endpoint: {request.url.path}")

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    provider = Trading212Provider()
    with (
        patch.object(Trading212Provider, "_client", fake_client),
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
    ):
        connection = await handle_oauth_callback(
            session,
            test_workspace.id,
            test_user.id,
            "demo:api-key:api-secret",
            provider_name="trading212",
        )

    cash = (
        await session.execute(
            select(Account).where(
                Account.connection_id == connection.id,
                Account.external_id == "trading212:123456789:cash",
            )
        )
    ).scalar_one()
    settlement = (
        await session.execute(
            select(Transaction).where(Transaction.external_id == "t212:settlement:fill-on-connect")
        )
    ).scalar_one()
    ledger = (
        await session.execute(
            select(AssetTransaction).where(AssetTransaction.external_id == "t212:fill:fill-on-connect")
        )
    ).scalar_one()

    assert cash.type == "investment"
    assert cash.currency == "USD"
    assert settlement.account_id == cash.id
    assert settlement.is_ignored is True
    assert settlement.type == "debit"
    assert settlement.amount == Decimal("201")
    assert ledger.kind == "buy"


@pytest.mark.asyncio
async def test_invalid_t212_positions_payload_does_not_archive_existing_holdings(
    session: AsyncSession, test_user: User, test_workspace
):
    """A malformed 200 response must be a provider error, never an empty portfolio."""
    credentials = {"api_key": "key", "api_secret": "secret"}
    connection = BankConnection(
        id=uuid.uuid4(),
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        provider="trading212",
        kind="brokerage",
        external_id="123",
        institution_name="Trading 212",
        credentials=credentials,
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(connection)
    await session.flush()
    holding = Asset(
        id=uuid.uuid4(),
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        connection_id=connection.id,
        source="trading212",
        external_id="trading212:position:AAPL_US_EQ",
        name="Apple Inc.",
        type="investment",
        currency="USD",
        is_archived=False,
    )
    session.add(holding)
    await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/positions")
        return httpx.Response(200, json={"unexpected": "payload"})

    async def fake_client(self, credentials=None):  # noqa: ANN001
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    provider = Trading212Provider()
    with (
        patch.object(Trading212Provider, "_client", fake_client),
        patch("app.services.connection_service.get_provider", return_value=provider),
    ):
        await _sync_holdings(session, test_user.id, connection, credentials)

    await session.commit()
    await session.refresh(holding)
    assert holding.is_archived is False
