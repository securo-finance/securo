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
from app.providers.base import (
    AccountData,
    ConnectionData,
    HoldingData,
    ProviderNotConfiguredError,
)
from app.providers.trading212 import Trading212Provider
from app.services.connection_service import (
    _sync_holdings,
    _sync_trading212_orders,
    handle_oauth_callback,
    sync_connection,
)


@pytest.mark.asyncio
async def test_order_history_with_currency_mismatch_keeps_live_holding_snapshot(
    session: AsyncSession, test_user: User, test_workspace
):
    """Order prices cannot replace an EUR live snapshot expressed in USD history."""
    connection = BankConnection(user_id=test_user.id, workspace_id=test_workspace.id, provider="trading212", kind="brokerage", external_id="123", institution_name="Trading 212", credentials={}, status="active")
    session.add(connection)
    await session.flush()
    asset = Asset(user_id=test_user.id, workspace_id=test_workspace.id, connection_id=connection.id, source="trading212", external_id="trading212:position:123:AAPL_US_EQ", name="Apple", type="investment", currency="EUR", units=Decimal("2"), purchase_price=Decimal("250"), valuation_method="manual")
    session.add(asset)
    await session.commit()
    await _sync_trading212_orders(session, test_user.id, connection, _OrdersProvider(), {})
    await session.flush()
    await session.refresh(asset)
    assert asset.units == Decimal("2")
    assert asset.purchase_price == Decimal("250")
    assert (await session.execute(select(AssetTransaction))).scalars().one().external_id == "t212:fill:fill-1"


@pytest.mark.asyncio
async def test_incomplete_order_history_keeps_live_holding_quantity_and_cost(
    session: AsyncSession, test_user: User, test_workspace
):
    """A one-fill history must not turn a two-share live position into one share."""
    connection = BankConnection(user_id=test_user.id, workspace_id=test_workspace.id, provider="trading212", kind="brokerage", external_id="123", institution_name="Trading 212", credentials={}, status="active")
    session.add(connection)
    await session.flush()
    asset = Asset(user_id=test_user.id, workspace_id=test_workspace.id, connection_id=connection.id, source="trading212", external_id="trading212:position:123:AAPL_US_EQ", name="Apple", type="investment", currency="USD", units=Decimal("2"), purchase_price=Decimal("250"), valuation_method="manual")
    session.add(asset)
    await session.commit()
    await _sync_trading212_orders(session, test_user.id, connection, _OrdersProvider(), {})
    await session.flush()
    await session.refresh(asset)
    assert asset.units == Decimal("2")
    assert asset.purchase_price == Decimal("250")


@pytest.mark.asyncio
async def test_asset_transaction_external_id_is_unique_per_asset(
    session: AsyncSession, test_user: User, test_workspace
):
    """The database, not only an importer pre-check, protects duplicate broker fills."""
    asset = Asset(user_id=test_user.id, workspace_id=test_workspace.id, name="Apple", type="investment")
    session.add(asset)
    await session.flush()
    values = dict(asset_id=asset.id, workspace_id=test_workspace.id, kind="buy", quantity=1, price=1, fee=0, date=datetime.now(timezone.utc).date(), source="trading212", external_id="t212:fill:1")
    session.add_all([AssetTransaction(**values), AssetTransaction(**values)])
    with pytest.raises(Exception):
        await session.flush()


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


@pytest.mark.asyncio
async def test_t212_reconnect_rejects_credentials_for_a_different_broker_account(
    session: AsyncSession, test_user: User, test_workspace
):
    connection = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        provider="trading212", kind="brokerage", external_id="live-account",
        institution_name="Trading 212", credentials={}, status="active",
    )
    session.add(connection)
    await session.commit()

    provider = AsyncMock()
    provider.kind = "brokerage"
    provider.handle_oauth_callback.return_value = __import__(
        "app.providers.base", fromlist=["ConnectionData"]
    ).ConnectionData("other-account", "Trading 212", {}, [])
    with patch("app.services.connection_service.get_provider", return_value=provider), pytest.raises(
        ValueError, match="different Trading 212 account"
    ):
        await handle_oauth_callback(
            session, test_workspace.id, test_user.id, "new:key", provider_name="trading212",
            reconnect_connection_id=connection.id,
        )

    await session.refresh(connection)
    assert connection.external_id == "live-account"


@pytest.mark.asyncio
async def test_t212_duplicate_connection_retry_requires_explicit_reconnect(
    session: AsyncSession, test_user: User, test_workspace
):
    """A repeated token submission must not import one broker account twice."""
    existing = BankConnection(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="trading212",
        kind="brokerage",
        external_id="account-123",
        institution_name="Trading 212",
        credentials={"api_key_enc": "opaque", "api_secret_enc": "opaque"},
        status="active",
    )
    session.add(existing)
    await session.commit()

    provider = AsyncMock()
    provider.kind = "brokerage"
    provider.handle_oauth_callback.return_value = ConnectionData(
        external_id="account-123",
        institution_name="Trading 212",
        credentials={"api_key_enc": "new", "api_secret_enc": "new"},
        accounts=[],
    )

    with (
        patch("app.services.connection_service.get_provider", return_value=provider),
        pytest.raises(ValueError, match="already connected.*reconnect"),
    ):
        await handle_oauth_callback(
            session,
            test_workspace.id,
            test_user.id,
            "new:key",
            provider_name="trading212",
        )

    connections = (
        await session.execute(
            select(BankConnection).where(
                BankConnection.workspace_id == test_workspace.id,
                BankConnection.provider == "trading212",
                BankConnection.external_id == "account-123",
            )
        )
    ).scalars().all()
    assert [connection.id for connection in connections] == [existing.id]


@pytest.mark.asyncio
async def test_t212_duplicate_connection_race_is_stopped_by_database_identity(
    session: AsyncSession, test_user: User, test_workspace
):
    """The unique index closes the gap between an absent lookup and insert."""
    existing = BankConnection(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="trading212",
        kind="brokerage",
        external_id="account-race",
        institution_name="Trading 212",
        credentials={"api_key_enc": "opaque", "api_secret_enc": "opaque"},
        status="active",
    )
    session.add(existing)
    await session.commit()
    existing_id = existing.id
    workspace_id = test_workspace.id

    provider = AsyncMock()
    provider.kind = "brokerage"
    provider.handle_oauth_callback.return_value = ConnectionData(
        external_id="account-race",
        institution_name="Trading 212",
        credentials={"api_key_enc": "new", "api_secret_enc": "new"},
        accounts=[],
    )

    with (
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch(
            "app.services.connection_service._t212_connection_exists",
            new_callable=AsyncMock,
            return_value=False,
        ),
        pytest.raises(ValueError, match="already connected.*reconnect"),
    ):
        await handle_oauth_callback(
            session,
            workspace_id,
            test_user.id,
            "new:key",
            provider_name="trading212",
        )

    connections = (
        await session.execute(
            select(BankConnection).where(
                BankConnection.workspace_id == workspace_id,
                BankConnection.provider == "trading212",
                BankConnection.external_id == "account-race",
            )
        )
    ).scalars().all()
    assert [connection.id for connection in connections] == [existing_id]


@pytest.mark.asyncio
async def test_t212_sync_rejects_provider_without_account_summary(
    session: AsyncSession, test_user: User, test_workspace
):
    """A bad registry entry must fail clearly before deriving broker identity."""
    connection = BankConnection(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="trading212",
        kind="brokerage",
        external_id="account-123",
        institution_name="Trading 212",
        credentials={"api_key_enc": "opaque", "api_secret_enc": "opaque"},
        status="active",
    )
    session.add(connection)
    await session.commit()

    class IncompleteProvider:
        async def refresh_credentials(self, credentials):
            return dict(credentials)

    with (
        patch(
            "app.services.connection_service.get_provider",
            return_value=IncompleteProvider(),
        ),
        pytest.raises(
            ProviderNotConfiguredError,
            match="does not implement account summary lookup",
        ),
    ):
        await sync_connection(
            session, connection.id, test_workspace.id, test_user.id
        )

    await session.refresh(connection)
    assert connection.status == "active"


@pytest.mark.asyncio
async def test_legacy_t212_sync_backfills_account_id_before_holdings_and_orders(
    session: AsyncSession, test_user: User, test_workspace
):
    """Legacy credentials must scope the snapshot and fills to one broker asset."""
    connection = BankConnection(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        provider="trading212",
        kind="brokerage",
        external_id="account-123",
        institution_name="Trading 212",
        credentials={"api_key_enc": "opaque", "api_secret_enc": "opaque"},
        status="active",
    )
    session.add(connection)
    await session.commit()

    class LegacyProvider:
        kind = "brokerage"

        async def refresh_credentials(self, credentials):
            return dict(credentials)

        async def get_institution_logo(self, credentials):
            return None

        async def get_account_summary(self, credentials):
            return {"id": "account-123", "currency": "USD", "cash": {}}

        async def get_accounts(self, credentials):
            return [
                AccountData(
                    external_id="trading212:account-123:cash",
                    name="Trading 212 Cash",
                    type="investment",
                    balance=Decimal("0"),
                    currency="USD",
                )
            ]

        async def get_transactions(self, credentials, account_external_id, since, payee_source="auto"):
            return []

        async def get_holdings(self, credentials):
            account_id = credentials.get("account_id")
            return [
                HoldingData(
                    external_id=(
                        f"trading212:position:{account_id}:AAPL_US_EQ"
                        if account_id
                        else "trading212:position:AAPL_US_EQ"
                    ),
                    name="Apple Inc.",
                    currency="USD",
                    current_value=Decimal("200"),
                    quantity=Decimal("2"),
                    purchase_price=Decimal("201"),
                    ticker="AAPL_US_EQ",
                )
            ]

        async def get_historical_orders(self, credentials):
            assert credentials["account_id"] == "account-123"
            return await _OrdersProvider().get_historical_orders(credentials)

    with patch("app.services.connection_service.get_provider", return_value=LegacyProvider()):
        await sync_connection(session, connection.id, test_workspace.id, test_user.id)

    await session.refresh(connection)
    assets = (
        await session.execute(
            select(Asset).where(Asset.connection_id == connection.id).order_by(Asset.external_id)
        )
    ).scalars().all()
    credentials = connection.credentials
    assert credentials is not None
    assert credentials["account_id"] == "account-123"
    assert [asset.external_id for asset in assets] == ["trading212:position:account-123:AAPL_US_EQ"]
    assert (await session.execute(select(AssetTransaction))).scalars().one().asset_id == assets[0].id
