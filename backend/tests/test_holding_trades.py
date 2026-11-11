"""Provider trade histories mirrored into synced holdings' ledgers."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.bank_connection import BankConnection
from app.models.user import User
from app.providers.base import BankProvider, HoldingData, HoldingTradeData
from app.providers.pluggy import _build_trade_data
from app.services.connection_service import _reconciled_trades, _sync_holding_trades


def _holding(quantity: str, *, withdrawn: bool = False, external_id: str = "h1") -> HoldingData:
    return HoldingData(
        external_id=external_id,
        name="Holding",
        currency="BRL",
        current_value=Decimal("100"),
        quantity=Decimal(quantity),
        is_withdrawn=withdrawn,
    )


def _trade(
    trade_id: str, kind: Literal["buy", "sell"], day: int, quantity: str, price: str = "1"
) -> HoldingTradeData:
    return HoldingTradeData(
        external_id=trade_id,
        kind=kind,
        date=date(2026, 8, day),
        quantity=Decimal(quantity),
        price=Decimal(price),
    )


def test_reconciled_trades_must_add_up_to_the_reported_position():
    trades = [_trade("b", "buy", 2, "228"), _trade("c", "buy", 28, "81")]
    assert _reconciled_trades(_holding("309"), trades) == trades
    # A missing trade leaves the ledger short of the position: not used.
    assert _reconciled_trades(_holding("309"), trades[:1]) is None


def test_reconciled_trades_drop_a_repeated_provider_row():
    """Pluggy listed the same 08-25 redemption twice under two ids."""
    trades = [
        _trade("buy", "buy", 4, "500000", "0.01"),
        _trade("s1", "sell", 25, "196961", "0.01007794"),
        _trade("s2", "sell", 25, "196961", "0.01007794"),
        _trade("s3", "sell", 25, "248947", "0.01007794"),
    ]
    reconciled = _reconciled_trades(_holding("54092"), trades)
    assert reconciled is not None
    assert [t.external_id for t in reconciled] == ["buy", "s1", "s3"]


def test_reconciled_trades_close_a_withdrawn_holding_at_zero():
    trades = [_trade("b", "buy", 1, "10"), _trade("s", "sell", 20, "10")]
    assert _reconciled_trades(_holding("10", withdrawn=True), trades) == trades


def test_pluggy_trade_keeps_cash_amount_exact_and_skips_income():
    trade = _build_trade_data(
        {
            "id": "t1",
            "type": "SELL",
            "tradeDate": "2026-08-25T00:00:00.000Z",
            "quantity": 196961,
            "value": 0.01007794,
            "amount": 1984.96,
            "netAmount": 1977.95,
            "expenses": {"brokerageFee": 1.5, "incomeTax": 7.01},
        },
    )
    assert trade is not None
    assert trade.kind == "sell"
    assert trade.quantity * trade.price == pytest.approx(Decimal("1984.96"))
    assert trade.fee == Decimal("1.5")
    assert (
        _build_trade_data(
            {"id": "t2", "type": "INTEREST", "date": "2026-09-11", "quantity": 11, "amount": 1.49}
        )
        is None
    )


async def _pluggy_holding(session: AsyncSession, user: User, workspace, name: str) -> Asset:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name=name,
        type="investment",
        currency="BRL",
        valuation_method="manual",
        source="pluggy",
        external_id="h1",
    )
    session.add(asset)
    await session.flush()
    return asset


async def _connection(session: AsyncSession, user: User, workspace) -> BankConnection:
    connection = BankConnection(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        provider="pluggy",
        external_id="item-1",
        institution_name="Broker",
        credentials={"item_id": "item-1"},
        settings={"holding_trades": {}},
        status="active",
        created_at=datetime.now(timezone.utc),
    )
    session.add(connection)
    await session.flush()
    return connection


async def _stored(session: AsyncSession, asset: Asset) -> list[tuple[str, str | None, float]]:
    rows = await session.execute(
        select(AssetTransaction.source, AssetTransaction.external_id, AssetTransaction.quantity)
        .where(AssetTransaction.asset_id == asset.id)
        .order_by(AssetTransaction.source, AssetTransaction.external_id)
    )
    return [(source, external_id, float(quantity)) for source, external_id, quantity in rows]


class _Provider:
    def __init__(self, trades: list[HoldingTradeData]) -> None:
        self.trades = trades
        self.requests = 0

    async def get_holding_trades(self, credentials, holdings):
        self.requests += 1
        return {holding.external_id: list(self.trades) for holding in holdings}


@pytest.mark.asyncio
async def test_sync_mirrors_reconciled_trades_and_leaves_manual_rows(
    session: AsyncSession, test_user: User, test_workspace
):
    asset = await _pluggy_holding(session, test_user, test_workspace, "AUPO11")
    session.add_all(
        [
            AssetTransaction(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                kind="buy",
                quantity=Decimal("1"),
                price=Decimal("1"),
                date=date(2026, 8, 1),
                source="pluggy",
                external_id="stale",
            ),
            AssetTransaction(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                kind="buy",
                quantity=Decimal("5"),
                price=Decimal("1"),
                date=date(2026, 8, 1),
                source="manual",
            ),
        ]
    )
    connection = await _connection(session, test_user, test_workspace)
    provider = _Provider(
        [_trade("b", "buy", 2, "228", "109.69"), _trade("c", "buy", 28, "81", "110.78")]
    )

    await _sync_holding_trades(
        session, connection, cast(BankProvider, provider), {}, [_holding("309")], {"h1": asset}
    )
    await session.flush()
    assert await _stored(session, asset) == [
        ("manual", None, 5.0),
        ("pluggy", "b", 228.0),
        ("pluggy", "c", 81.0),
    ]

    # The ledger now adds up to the position: the next sync asks for nothing.
    await _sync_holding_trades(
        session, connection, cast(BankProvider, provider), {}, [_holding("309")], {"h1": asset}
    )
    assert provider.requests == 1


@pytest.mark.asyncio
async def test_sync_keeps_stored_history_the_provider_no_longer_returns(
    session: AsyncSession, test_user: User, test_workspace
):
    """A provider that only returns recent trades still keeps a full ledger."""
    asset = await _pluggy_holding(session, test_user, test_workspace, "SPYL")
    session.add(
        AssetTransaction(
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            kind="buy",
            quantity=Decimal("30.8512"),
            price=Decimal("18.96"),
            date=date(2026, 8, 24),
            source="pluggy",
            external_id="t1",
        )
    )
    connection = await _connection(session, test_user, test_workspace)
    provider = _Provider([_trade("t2", "buy", 28, "39.969", "19.08")])

    await _sync_holding_trades(
        session, connection, cast(BankProvider, provider), {}, [_holding("70.8202")], {"h1": asset}
    )
    await session.flush()
    assert await _stored(session, asset) == [("pluggy", "t1", 30.8512), ("pluggy", "t2", 39.969)]
