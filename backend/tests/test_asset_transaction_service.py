"""Tests for the investment transaction ledger (issue #235).

Covers the weighted-average (preço médio) algorithm and the service paths:
buy/sell recompute, realized gains, find-or-create consolidation by ticker.
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.user import User
from app.providers.market_price import MarketPriceProvider
from app.schemas.asset import (
    AssetBuyCreate,
    AssetTransactionCreate,
    AssetTransactionUpdate,
    MarketSymbolMatch,
    MarketSymbolQuote,
)
from app.services import asset_transaction_service
from app.services.asset_transaction_service import _recompute


# ---------------------------------------------------------------------------
# Pure algorithm: _recompute (no DB)
# ---------------------------------------------------------------------------

def _tx(kind: str, qty: str, price: str, d: date, fee: str = "0") -> AssetTransaction:
    return AssetTransaction(
        id=uuid.uuid4(), asset_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
        kind=kind, quantity=Decimal(qty), price=Decimal(price), fee=Decimal(fee), date=d,
    )


def test_recompute_weighted_average_across_buys():
    # 10 @ 100 then 5 @ 110 → avg = (1000 + 550) / 15 = 103.3333...
    pos = _recompute([
        _tx("buy", "10", "100", date(2026, 1, 1)),
        _tx("buy", "5", "110", date(2026, 2, 1)),
    ])
    assert pos["units"] == Decimal("15")
    assert pos["average_price"].quantize(Decimal("0.0001")) == Decimal("103.3333")
    assert pos["cost_basis"] == Decimal("1550")
    assert pos["realized_gain"] == Decimal("0")


def test_recompute_partial_sell_keeps_average_and_realizes():
    # buy 10 @ 100, buy 5 @ 110 (avg 103.3333), sell 6 @ 130
    # realized = (130 - 103.3333) * 6 = 160.0002 (≈ 160), avg unchanged
    pos = _recompute([
        _tx("buy", "10", "100", date(2026, 1, 1)),
        _tx("buy", "5", "110", date(2026, 1, 2)),
        _tx("sell", "6", "130", date(2026, 3, 1)),
    ])
    assert pos["units"] == Decimal("9")
    assert pos["average_price"].quantize(Decimal("0.0001")) == Decimal("103.3333")
    assert pos["realized_gain"].quantize(Decimal("0.01")) == Decimal("160.00")


def test_recompute_sell_all_flattens_position():
    pos = _recompute([
        _tx("buy", "10", "100", date(2026, 1, 1)),
        _tx("sell", "10", "120", date(2026, 2, 1)),
    ])
    assert pos["units"] == Decimal("0")
    assert pos["average_price"] is None
    assert pos["cost_basis"] == Decimal("0")
    assert pos["realized_gain"].quantize(Decimal("0.01")) == Decimal("200.00")


def test_recompute_includes_fees_in_cost_basis():
    # buy 10 @ 100 with 9.90 fee → cost basis 1009.90, avg 100.99
    pos = _recompute([_tx("buy", "10", "100", date(2026, 1, 1), fee="9.90")])
    assert pos["cost_basis"] == Decimal("1009.90")
    assert pos["average_price"].quantize(Decimal("0.0001")) == Decimal("100.9900")


def test_recompute_clamps_oversell():
    # Selling more than held shouldn't drive quantity negative.
    pos = _recompute([
        _tx("buy", "5", "100", date(2026, 1, 1)),
        _tx("sell", "10", "120", date(2026, 2, 1)),
    ])
    assert pos["units"] == Decimal("0")
    assert pos["average_price"] is None


def test_recompute_orders_by_date_not_insertion():
    # A backdated buy must be processed first.
    pos = _recompute([
        _tx("sell", "5", "130", date(2026, 3, 1)),
        _tx("buy", "10", "100", date(2026, 1, 1)),
    ])
    assert pos["units"] == Decimal("5")
    assert pos["average_price"].quantize(Decimal("0.01")) == Decimal("100.00")


# ---------------------------------------------------------------------------
# Service paths (DB)
# ---------------------------------------------------------------------------

class _FakeProvider(MarketPriceProvider):
    name = "fake"

    def __init__(self, quotes: dict[str, MarketSymbolQuote]):
        self._quotes = quotes

    async def search(self, query: str, limit: int = 20) -> list[MarketSymbolMatch]:
        return []

    async def get_quote(self, symbol: str) -> Optional[MarketSymbolQuote]:
        return self._quotes.get(symbol.upper())

    async def get_latest_prices(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        return {s.upper(): Decimal(str(self._quotes[s.upper()].price)) for s in symbols if s.upper() in self._quotes}


def _quote(symbol: str, price: float, currency: str = "BRL") -> MarketSymbolQuote:
    return MarketSymbolQuote(
        symbol=symbol, name=f"{symbol} SA", exchange="SAO",
        currency=currency, price=price, quote_type="EQUITY",
    )


@pytest_asyncio.fixture
async def market_asset(session: AsyncSession, test_user: User, test_workspace) -> Asset:
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Petrobras", type="stock", currency="BRL",
        valuation_method="market_price", ticker="PETR4.SA",
        last_price=Decimal("30.00"),
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


@pytest.mark.asyncio
async def test_add_buys_sets_units_average_and_cost_basis(session, test_workspace, market_asset):
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    read = await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("5"), price=Decimal("26"), date=date(2026, 2, 1)),
    )
    # avg = (200 + 130) / 15 = 22.00
    assert read.units == 15
    assert round(read.average_price, 2) == 22.00
    assert round(read.total_invested, 2) == 330.00
    assert read.transaction_count == 2


@pytest.mark.asyncio
async def test_sell_records_realized_gain(session, test_workspace, market_asset):
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    read = await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="sell", quantity=Decimal("4"), price=Decimal("30"), date=date(2026, 3, 1)),
    )
    assert read.units == 6
    assert round(read.average_price, 2) == 20.00  # average unchanged by sell
    assert round(read.realized_gain, 2) == 40.00  # (30 - 20) * 4


@pytest.mark.asyncio
async def test_delete_transaction_recomputes(session, test_workspace, market_asset):
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    txs = await asset_transaction_service.list_asset_transactions(session, market_asset.id, test_workspace.id)
    read = await asset_transaction_service.delete_transaction(session, txs[0].id, test_workspace.id)
    assert read.units == 0
    assert read.average_price is None


@pytest.mark.asyncio
async def test_update_transaction_recomputes(session, test_workspace, market_asset):
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    txs = await asset_transaction_service.list_asset_transactions(session, market_asset.id, test_workspace.id)
    read = await asset_transaction_service.update_transaction(
        session, txs[0].id, test_workspace.id, AssetTransactionUpdate(quantity=Decimal("20")),
    )
    assert read.units == 20
    assert round(read.average_price, 2) == 20.00


@pytest.mark.asyncio
async def test_oversell_is_rejected(session, test_workspace, market_asset):
    from fastapi import HTTPException

    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    with pytest.raises(HTTPException) as exc:
        await asset_transaction_service.add_transaction(
            session, market_asset.id, test_workspace.id,
            AssetTransactionCreate(kind="sell", quantity=Decimal("11"), price=Decimal("30"), date=date(2026, 2, 1)),
        )
    assert exc.value.status_code == 422
    # The rejected sell must not have changed the position.
    txs = await asset_transaction_service.list_asset_transactions(session, market_asset.id, test_workspace.id)
    assert len(txs) == 1


@pytest.mark.asyncio
async def test_sell_exact_holding_is_allowed(session, test_workspace, market_asset):
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    read = await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="sell", quantity=Decimal("10"), price=Decimal("30"), date=date(2026, 2, 1)),
    )
    assert read.units == 0


@pytest.mark.asyncio
async def test_sell_before_any_buy_is_rejected(session, test_workspace, market_asset):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await asset_transaction_service.add_transaction(
            session, market_asset.id, test_workspace.id,
            AssetTransactionCreate(kind="sell", quantity=Decimal("5"), price=Decimal("30"), date=date(2026, 1, 1)),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_full_exit_marks_sold(session, test_workspace, market_asset):
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    read = await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="sell", quantity=Decimal("10"), price=Decimal("30"), date=date(2026, 6, 1)),
    )
    assert read.units == 0
    assert read.average_price is None
    assert read.sell_date == date(2026, 6, 1)  # drops out of the active portfolio
    assert round(read.realized_gain, 2) == 100.00


@pytest.mark.asyncio
async def test_rebuy_after_full_exit_resets_position(session, test_workspace, market_asset):
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="sell", quantity=Decimal("10"), price=Decimal("30"), date=date(2026, 2, 1)),
    )
    read = await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("5"), price=Decimal("40"), date=date(2026, 3, 1)),
    )
    assert read.units == 5
    assert round(read.average_price, 2) == 40.00
    assert read.sell_date is None  # re-entered → no longer "sold"
    # Realized gain from the earlier round-trip is retained.
    assert round(read.realized_gain, 2) == 100.00


@pytest.mark.asyncio
async def test_multiple_sells_accumulate_realized_gain(session, test_workspace, market_asset):
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="sell", quantity=Decimal("3"), price=Decimal("30"), date=date(2026, 2, 1)),
    )
    read = await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="sell", quantity=Decimal("2"), price=Decimal("25"), date=date(2026, 3, 1)),
    )
    # (30-20)*3 + (25-20)*2 = 30 + 10 = 40
    assert read.units == 5
    assert round(read.realized_gain, 2) == 40.00


@pytest.mark.asyncio
async def test_buy_into_holding_separate_across_wallets(session, test_workspace, test_user):
    from app.models.asset_group import AssetGroup

    wallet = AssetGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Broker A", icon="wallet", color="#0EA5E9", position=0, source="manual",
    )
    session.add(wallet)
    await session.commit()

    provider = _FakeProvider({"ITUB4.SA": _quote("ITUB4.SA", 30.0)})
    ungrouped = await asset_transaction_service.buy_into_holding(
        session, test_workspace.id, test_user.id,
        AssetBuyCreate(ticker="ITUB4.SA", quantity=Decimal("10"), price=Decimal("28"), date=date(2026, 1, 1)),
        market_provider=provider,
    )
    walleted = await asset_transaction_service.buy_into_holding(
        session, test_workspace.id, test_user.id,
        AssetBuyCreate(ticker="ITUB4.SA", quantity=Decimal("5"), price=Decimal("32"), date=date(2026, 2, 1), group_id=wallet.id),
        market_provider=provider,
    )
    # Same ticker, different wallet → distinct holdings (not consolidated).
    assert ungrouped.id != walleted.id
    rows = (
        await session.execute(
            select(Asset).where(Asset.workspace_id == test_workspace.id, Asset.ticker == "ITUB4.SA")
        )
    ).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_buy_into_holding_consolidates_by_ticker(session, test_workspace, test_user):
    provider = _FakeProvider({"VALE3.SA": _quote("VALE3.SA", 60.0)})
    first = await asset_transaction_service.buy_into_holding(
        session, test_workspace.id, test_user.id,
        AssetBuyCreate(ticker="VALE3.SA", quantity=Decimal("10"), price=Decimal("50"), date=date(2026, 1, 1)),
        market_provider=provider,
    )
    second = await asset_transaction_service.buy_into_holding(
        session, test_workspace.id, test_user.id,
        AssetBuyCreate(ticker="VALE3.SA", quantity=Decimal("10"), price=Decimal("70"), date=date(2026, 2, 1)),
        market_provider=provider,
    )
    # Same logical holding — not two assets.
    assert first.id == second.id
    assert second.units == 20
    assert round(second.average_price, 2) == 60.00

    all_vale = (
        await session.execute(
            select(Asset).where(Asset.workspace_id == test_workspace.id, Asset.ticker == "VALE3.SA")
        )
    ).scalars().all()
    assert len(all_vale) == 1
