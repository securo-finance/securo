"""Tests for the investment transaction ledger (issue #235).

Covers the weighted-average (preço médio) algorithm and the service paths:
buy/sell recompute, realized gains, find-or-create consolidation by ticker.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today, use_resolved_timezone
from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.models.user import User
from app.providers.market_price import MarketPriceProvider
from app.schemas.asset import (
    AssetBuyCreate,
    AssetCreate,
    AssetTransactionCreate,
    AssetTransactionUpdate,
    MarketSymbolMatch,
    MarketSymbolQuote,
)
from app.services import asset_import_service, asset_service, asset_transaction_service
from app.services.asset_transaction_service import _detect_oversell, _recompute


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
# Stock splits (issue #944): pure replay
# ---------------------------------------------------------------------------

def _split(old: int, new: int, d: date) -> AssetTransaction:
    return AssetTransaction(
        id=uuid.uuid4(), asset_id=uuid.uuid4(), workspace_id=uuid.uuid4(),
        kind="split", quantity=Decimal("0"), price=Decimal("0"), fee=Decimal("0"),
        old_units=old, new_units=new, date=d,
    )


def test_recompute_split_multiplies_units_and_keeps_the_cost():
    # buy 10 @ 100, sell 4 @ 130 (gain 120), then a 100-for-1 split: the 6 left
    # become 600 while the 600 of cost and the 120 gain stay, so the average
    # price falls from 100 to 1.
    pos = _recompute([
        _tx("buy", "10", "100", date(2026, 1, 1)),
        _tx("sell", "4", "130", date(2026, 2, 1)),
        _split(1, 100, date(2026, 3, 1)),
    ])
    assert pos["units"] == Decimal("600")
    assert pos["cost_basis"] == Decimal("600")
    assert pos["realized_gain"] == Decimal("120")
    assert pos["average_price"] == Decimal("1")


def test_recompute_reverse_split_divides_units_and_multiplies_the_average():
    # 100 @ 5 through a 1-for-10 reverse split (10 → 1): 10 units, same 500 cost.
    pos = _recompute([
        _tx("buy", "100", "5", date(2026, 1, 1)),
        _split(10, 1, date(2026, 2, 1)),
    ])
    assert pos["units"] == Decimal("10")
    assert pos["cost_basis"] == Decimal("500")
    assert pos["average_price"] == Decimal("50")


def test_recompute_sell_after_a_split_realizes_against_the_new_average():
    # buy 10 @ 100, 1 → 2 split (20 units, average 50), sell 5 @ 60.
    pos = _recompute([
        _tx("buy", "10", "100", date(2026, 1, 1)),
        _split(1, 2, date(2026, 2, 1)),
        _tx("sell", "5", "60", date(2026, 3, 1)),
    ])
    assert pos["units"] == Decimal("15")
    assert pos["average_price"] == Decimal("50")
    assert pos["realized_gain"] == Decimal("50")  # (60 - 50) * 5


def test_recompute_applies_a_split_before_the_trades_of_its_day():
    # The sell was entered before the split, but both are dated the same day and
    # the split still comes first: 10 → 20 units, then 15 sold. Replayed in entry
    # order the sell would exceed the 10 held and be clamped to it.
    buy = _tx("buy", "10", "100", date(2026, 1, 1))
    sell = _tx("sell", "15", "60", date(2026, 1, 2))
    split = _split(1, 2, date(2026, 1, 2))
    sell.created_at = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
    split.created_at = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)

    pos = _recompute([sell, split, buy])

    assert pos["units"] == Decimal("5")
    assert pos["realized_gain"] == Decimal("150")  # (60 - 50) * 15
    assert _detect_oversell([sell, split, buy]) is None


@pytest.mark.parametrize(
    "held,after",
    [("10", "3.333333"), ("20", "6.666666")],  # the second would round to ...667
)
def test_recompute_rounds_split_units_down_to_six_decimals(held, after):
    # A 3 → 1 reverse split leaves repeating decimals; rounded down, the position
    # can still be sold out whole.
    buy = _tx("buy", held, "10", date(2026, 1, 1))
    split = _split(3, 1, date(2026, 2, 1))
    assert _recompute([buy, split])["units"] == Decimal(after)

    sell = _tx("sell", after, "12", date(2026, 3, 1))
    pos = _recompute([buy, split, sell])
    assert pos["units"] == Decimal("0")
    assert pos["average_price"] is None
    assert _detect_oversell([buy, split, sell]) is None


def test_recompute_successive_splits_accumulate():
    pos = _recompute([
        _tx("buy", "10", "100", date(2026, 1, 1)),
        _split(1, 2, date(2026, 2, 1)),
        _split(1, 5, date(2026, 3, 1)),
    ])
    assert pos["units"] == Decimal("100")  # x10
    assert pos["average_price"] == Decimal("10")


def test_recompute_split_only_scales_the_units_held_on_its_date():
    # A buy dated after the split is already at the new share count.
    pos = _recompute([
        _split(1, 100, date(2026, 1, 1)),
        _tx("buy", "10", "5", date(2026, 2, 1)),
    ])
    assert pos["units"] == Decimal("10")


def test_detect_oversell_counts_the_units_after_a_split():
    ledger = [_tx("buy", "10", "100", date(2026, 1, 1)), _split(1, 2, date(2026, 2, 1))]
    # 15 is more than the 10 bought but fewer than the 20 held after the split.
    assert _detect_oversell(ledger + [_tx("sell", "15", "60", date(2026, 3, 1))]) is None
    assert _detect_oversell(ledger + [_tx("sell", "21", "60", date(2026, 3, 1))]) == (
        Decimal("21"), Decimal("20"),
    )


def test_detect_oversell_after_a_reverse_split():
    ledger = [_tx("buy", "10", "100", date(2026, 1, 1)), _split(10, 1, date(2026, 2, 1))]
    assert _detect_oversell(ledger + [_tx("sell", "5", "60", date(2026, 3, 1))]) == (
        Decimal("5"), Decimal("1"),
    )


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
    assert read is not None
    assert read.units == 15

    assert read.average_price is not None
    assert round(read.average_price, 2) == 22.00
    assert read.total_invested is not None
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
    assert read is not None
    assert read.units == 6
    assert read.average_price is not None
    assert round(read.average_price, 2) == 20.00  # average unchanged by sell
    assert read.realized_gain is not None
    assert round(read.realized_gain, 2) == 40.00  # (30 - 20) * 4


@pytest.mark.asyncio
async def test_delete_transaction_recomputes(session, test_workspace, market_asset):
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    txs = await asset_transaction_service.list_asset_transactions(session, market_asset.id, test_workspace.id)
    assert txs is not None
    read = await asset_transaction_service.delete_transaction(session, txs[0].id, test_workspace.id)
    assert read is not None
    assert read.units == 0
    assert read.average_price is None


@pytest.mark.asyncio
async def test_update_transaction_recomputes(session, test_workspace, market_asset):
    await asset_transaction_service.add_transaction(
        session, market_asset.id, test_workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal("10"), price=Decimal("20"), date=date(2026, 1, 1)),
    )
    txs = await asset_transaction_service.list_asset_transactions(session, market_asset.id, test_workspace.id)
    assert txs is not None
    read = await asset_transaction_service.update_transaction(
        session, txs[0].id, test_workspace.id, AssetTransactionUpdate(quantity=Decimal("20")),
    )
    assert read is not None
    assert read.units == 20
    assert read.average_price is not None
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
    assert txs is not None
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
    assert read is not None
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
    assert read is not None
    assert read.units == 0
    assert read.average_price is None
    assert read.sell_date == date(2026, 6, 1)  # drops out of the active portfolio
    assert read.realized_gain is not None
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
    assert read is not None
    assert read.units == 5
    assert read.average_price is not None
    assert round(read.average_price, 2) == 40.00
    assert read.sell_date is None  # re-entered → no longer "sold"
    # Realized gain from the earlier round-trip is retained.
    assert read.realized_gain is not None
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
    assert read is not None
    assert read.units == 5
    assert read.realized_gain is not None
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
    assert second is not None
    assert second.units == 20
    assert second.average_price is not None
    assert round(second.average_price, 2) == 60.00

    all_vale = (
        await session.execute(
            select(Asset).where(Asset.workspace_id == test_workspace.id, Asset.ticker == "VALE3.SA")
        )
    ).scalars().all()
    assert len(all_vale) == 1


# ---------------------------------------------------------------------------
# Stock splits (issue #944): service paths (DB)
# ---------------------------------------------------------------------------

_RATIO_RANGE = "old_units and new_units must be whole numbers from 1 to 1000000"
_NO_MONEY = "a split has no quantity, price or fee"
_FUTURE_SPLIT = (
    "A split can't be dated in the future. "
    "Record it once the holding trades at the new price."
)


async def _buy(session, workspace, asset, quantity: str, price: str, d: date):
    return await asset_transaction_service.add_transaction(
        session, asset.id, workspace.id,
        AssetTransactionCreate(kind="buy", quantity=Decimal(quantity), price=Decimal(price), date=d),
    )


async def _sell(session, workspace, asset, quantity: str, price: str, d: date):
    return await asset_transaction_service.add_transaction(
        session, asset.id, workspace.id,
        AssetTransactionCreate(kind="sell", quantity=Decimal(quantity), price=Decimal(price), date=d),
    )


async def _add_split(session, workspace, asset, old: Optional[int], new: Optional[int], d: date, **extra):
    return await asset_transaction_service.add_transaction(
        session, asset.id, workspace.id,
        AssetTransactionCreate(kind="split", old_units=old, new_units=new, date=d, **extra),
    )


async def _ledger(session, workspace, asset):
    """The holding's entries, newest first."""
    txs = await asset_transaction_service.list_asset_transactions(session, asset.id, workspace.id)
    assert txs is not None
    return txs


async def _split_ratio(session, tx_id: uuid.UUID) -> tuple[Optional[int], Optional[int]]:
    """The ratio as stored: autoflush writes any edit still pending in the session."""
    row = (
        await session.execute(
            select(AssetTransaction.old_units, AssetTransaction.new_units)
            .where(AssetTransaction.id == tx_id)
        )
    ).one()
    return row.old_units, row.new_units


@pytest.mark.asyncio
async def test_add_split_scales_units_and_keeps_the_cost(session, test_workspace, market_asset):
    await _buy(session, test_workspace, market_asset, "10", "100", date(2026, 1, 1))
    await _sell(session, test_workspace, market_asset, "4", "130", date(2026, 2, 1))
    read = await _add_split(session, test_workspace, market_asset, 1, 100, date(2026, 3, 1))

    assert read is not None
    assert read.units == 600
    assert read.average_price == pytest.approx(1.0)  # 100 ÷ 100
    assert read.purchase_price == pytest.approx(600)  # cost basis of the held units
    assert read.total_invested == pytest.approx(600)
    assert read.realized_gain == pytest.approx(120)  # untouched by the split
    assert read.transaction_count == 3

    # A split moves no money: it is stored with zero quantity, price and fee.
    row = (
        await session.execute(select(AssetTransaction).where(AssetTransaction.kind == "split"))
    ).scalar_one()
    assert (row.quantity, row.price, row.fee) == (0, 0, 0)
    assert (row.old_units, row.new_units) == (1, 100)
    assert row.source == "manual"


@pytest.mark.asyncio
async def test_split_accepts_the_largest_ratio(session, test_workspace, market_asset):
    await _buy(session, test_workspace, market_asset, "10", "100", date(2026, 1, 1))
    read = await _add_split(session, test_workspace, market_asset, 1, 1_000_000, date(2026, 2, 1))
    assert read is not None
    assert read.units == 10_000_000


@pytest.mark.parametrize("synced", [False, True], ids=["manual", "synced"])
@pytest.mark.asyncio
async def test_split_is_refused_on_holdings_the_ledger_does_not_drive(
    session, test_user, test_workspace, test_connection, synced
):
    asset = Asset(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Held elsewhere", type="stock", currency="BRL",
        valuation_method="market_price" if synced else "manual",
        ticker="PETR4.SA" if synced else None,
        connection_id=test_connection.id if synced else None,
    )
    session.add(asset)
    await session.commit()
    # Units are on record, so the refusal is about the holding, not the ledger.
    await _buy(session, test_workspace, asset, "10", "20", date(2026, 1, 1))

    with pytest.raises(HTTPException) as exc:
        await _add_split(session, test_workspace, asset, 1, 100, date(2026, 2, 1))

    assert exc.value.status_code == 422
    assert exc.value.detail == (
        "Splits can only be recorded on market-priced holdings "
        "that aren't synced from a bank connection."
    )
    assert len(await _ledger(session, test_workspace, asset)) == 1


@pytest.mark.parametrize(
    "kind,fields,detail",
    [
        ("split", {}, _RATIO_RANGE),
        ("split", {"old_units": 1}, _RATIO_RANGE),
        ("split", {"new_units": 100}, _RATIO_RANGE),
        ("split", {"old_units": 0, "new_units": 2}, _RATIO_RANGE),
        ("split", {"old_units": 1, "new_units": -5}, _RATIO_RANGE),
        ("split", {"old_units": 1, "new_units": 1_000_001}, _RATIO_RANGE),
        ("split", {"old_units": 1_000_001, "new_units": 1}, _RATIO_RANGE),
        ("split", {"old_units": 2, "new_units": 2}, "old_units and new_units must differ"),
        ("split", {"old_units": 1, "new_units": 2, "quantity": Decimal("5")}, _NO_MONEY),
        ("split", {"old_units": 1, "new_units": 2, "price": Decimal("1")}, _NO_MONEY),
        ("split", {"old_units": 1, "new_units": 2, "fee": Decimal("1")}, _NO_MONEY),
        (
            "buy",
            {"quantity": Decimal("1"), "price": Decimal("1"), "old_units": 1},
            "old_units and new_units only apply to a split",
        ),
        (
            "sell",
            {"quantity": Decimal("1"), "price": Decimal("1"), "new_units": 2},
            "old_units and new_units only apply to a split",
        ),
        (
            "gift",
            {"quantity": Decimal("1"), "price": Decimal("1")},
            "kind must be 'buy', 'sell' or 'split'",
        ),
    ],
)
@pytest.mark.asyncio
async def test_add_transaction_validation_messages(
    session, test_workspace, market_asset, kind, fields, detail
):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 1, 1))

    with pytest.raises(HTTPException) as exc:
        await asset_transaction_service.add_transaction(
            session, market_asset.id, test_workspace.id,
            AssetTransactionCreate(kind=kind, date=date(2026, 2, 1), **fields),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == detail
    assert len(await _ledger(session, test_workspace, market_asset)) == 1


@pytest.mark.parametrize(
    "split_date",
    [date(2026, 1, 1), date(2026, 2, 1)],
    ids=["before the first buy", "on the day of the first buy"],
)
@pytest.mark.asyncio
async def test_split_needs_units_held_before_its_date(
    session, test_workspace, market_asset, split_date
):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 2, 1))

    with pytest.raises(HTTPException) as exc:
        await _add_split(session, test_workspace, market_asset, 1, 100, split_date)

    assert exc.value.status_code == 422
    assert exc.value.detail == (
        f"No units are held before {split_date.isoformat()}. "
        "Record the buys before the split first."
    )
    assert len(await _ledger(session, test_workspace, market_asset)) == 1


@pytest.mark.asyncio
async def test_split_dated_in_the_future_is_refused(session, test_workspace, market_asset):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 1, 1))

    with pytest.raises(HTTPException) as exc:
        await _add_split(
            session, test_workspace, market_asset, 1, 100, app_today() + timedelta(days=1),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == _FUTURE_SPLIT
    assert len(await _ledger(session, test_workspace, market_asset)) == 1


@pytest.mark.asyncio
async def test_split_dated_today_is_accepted(session, test_workspace, market_asset):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 1, 1))

    read = await _add_split(session, test_workspace, market_asset, 1, 100, app_today())

    assert read is not None
    assert read.units == 1000
    assert read.transaction_count == 2


class _NoonOnOctober30(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc).astimezone(tz)


@pytest.mark.asyncio
async def test_split_date_follows_the_workspace_calendar(session, test_workspace, market_asset):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 1, 1))

    # Noon UTC on the 30th is already the 31st in Kiritimati (UTC+14).
    with (
        patch("app.core.app_clock.datetime", _NoonOnOctober30),
        use_resolved_timezone(ZoneInfo("Pacific/Kiritimati")),
    ):
        read = await _add_split(session, test_workspace, market_asset, 1, 100, date(2026, 10, 31))
        with pytest.raises(HTTPException) as exc:
            await _add_split(session, test_workspace, market_asset, 1, 2, date(2026, 11, 1))

    assert read is not None
    assert read.units == 1000
    assert exc.value.detail == _FUTURE_SPLIT


@pytest.mark.asyncio
async def test_buy_dated_in_the_future_is_still_accepted(session, test_workspace, market_asset):
    # Only a split has to wait for its date; the rule doesn't reach the trades.
    read = await _buy(
        session, test_workspace, market_asset, "10", "20", app_today() + timedelta(days=3),
    )

    assert read is not None
    assert read.units == 10


@pytest.mark.asyncio
async def test_split_is_refused_once_the_position_is_closed(session, test_workspace, market_asset):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 1, 1))
    await _sell(session, test_workspace, market_asset, "10", "30", date(2026, 2, 1))

    with pytest.raises(HTTPException) as exc:
        await _add_split(session, test_workspace, market_asset, 1, 100, date(2026, 3, 1))

    assert exc.value.status_code == 422
    assert exc.value.detail.startswith("No units are held before 2026-03-01.")


@pytest.mark.asyncio
async def test_split_on_a_holding_without_a_ledger_keeps_its_units(
    session, test_workspace, market_asset
):
    # A pre-ledger "no cost" holding has units but no buys; replaying a split
    # over an empty ledger would recompute those units to zero.
    market_asset.units = Decimal("50")
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await _add_split(session, test_workspace, market_asset, 1, 100, date(2026, 3, 1))

    assert exc.value.status_code == 422
    await session.refresh(market_asset)
    assert market_asset.units == Decimal("50")
    assert await _ledger(session, test_workspace, market_asset) == []


@pytest.mark.asyncio
async def test_reverse_split_that_leaves_a_later_sell_short_is_refused(
    session, test_workspace, market_asset
):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 1, 1))
    await _sell(session, test_workspace, market_asset, "6", "30", date(2026, 3, 1))

    # 10 → 1 leaves a single unit by the time of the sell.
    with pytest.raises(HTTPException) as exc:
        await _add_split(session, test_workspace, market_asset, 10, 1, date(2026, 2, 1))

    assert exc.value.status_code == 422
    assert exc.value.detail == (
        "Cannot sell 6 units — only 1 held at that date. Short positions aren't supported."
    )
    assert len(await _ledger(session, test_workspace, market_asset)) == 2
    await session.refresh(market_asset)
    assert market_asset.units == Decimal("4")


@pytest.mark.asyncio
async def test_editing_a_split_ratio_recomputes_the_position(session, test_workspace, market_asset):
    await _buy(session, test_workspace, market_asset, "10", "480", date(2026, 1, 1))
    await _add_split(session, test_workspace, market_asset, 1, 100, date(2026, 2, 1))
    split = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.kind == "split")

    read = await asset_transaction_service.update_transaction(
        session, split.id, test_workspace.id, AssetTransactionUpdate(old_units=1, new_units=10),
    )

    assert read is not None
    assert read.units == 100
    assert read.average_price == pytest.approx(48.0)
    assert read.total_invested == pytest.approx(4800)
    assert await _split_ratio(session, split.id) == (1, 10)


@pytest.mark.asyncio
async def test_editing_a_split_with_null_trade_fields_keeps_them_at_zero(
    session, test_workspace, market_asset
):
    await _buy(session, test_workspace, market_asset, "10", "480", date(2026, 1, 1))
    await _add_split(session, test_workspace, market_asset, 1, 100, date(2026, 2, 1))
    split = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.kind == "split")

    # An explicit null is a set field, and these columns are NOT NULL.
    read = await asset_transaction_service.update_transaction(
        session,
        split.id,
        test_workspace.id,
        AssetTransactionUpdate(quantity=None, price=None, fee=None, new_units=10),
    )

    assert read is not None
    assert read.units == 100
    saved = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.kind == "split")
    assert (saved.quantity, saved.price, saved.fee) == (0, 0, 0)


@pytest.mark.asyncio
async def test_editing_a_split_down_under_a_later_sell_is_refused_without_changes(
    session, test_workspace, market_asset
):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 1, 1))
    await _add_split(session, test_workspace, market_asset, 1, 10, date(2026, 2, 1))  # 100 units
    await _sell(session, test_workspace, market_asset, "60", "3", date(2026, 3, 1))
    split = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.kind == "split")

    # 1 → 2 would leave 20 units for a sell of 60.
    with pytest.raises(HTTPException) as exc:
        await asset_transaction_service.update_transaction(
            session, split.id, test_workspace.id, AssetTransactionUpdate(old_units=1, new_units=2),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail.startswith("Cannot sell 60 units — only 20 held at that date.")
    assert not session.dirty
    assert await _split_ratio(session, split.id) == (1, 10)
    await session.refresh(market_asset)
    assert market_asset.units == Decimal("40")


@pytest.mark.parametrize(
    "from_kind,patch",
    [
        ("split", {"kind": "buy", "quantity": Decimal("1"), "price": Decimal("1")}),
        ("split", {"kind": "sell", "quantity": Decimal("1"), "price": Decimal("1")}),
        ("buy", {"kind": "split", "old_units": 1, "new_units": 2}),
        ("sell", {"kind": "split", "old_units": 1, "new_units": 2}),
    ],
)
@pytest.mark.asyncio
async def test_a_split_cannot_become_a_trade_or_the_other_way_round(
    session, test_workspace, market_asset, from_kind, patch
):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 1, 1))
    await _add_split(session, test_workspace, market_asset, 1, 2, date(2026, 2, 1))
    await _sell(session, test_workspace, market_asset, "5", "30", date(2026, 3, 1))
    target = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.kind == from_kind)

    with pytest.raises(HTTPException) as exc:
        await asset_transaction_service.update_transaction(
            session, target.id, test_workspace.id, AssetTransactionUpdate(**patch),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == (
        "A split can't become a buy or a sell, or the other way round. "
        "Delete it and add a new entry."
    )
    assert not session.dirty
    after = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.id == target.id)
    assert after.kind == from_kind


@pytest.mark.asyncio
async def test_moving_a_split_before_the_first_buy_is_refused(session, test_workspace, market_asset):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 2, 1))
    await _add_split(session, test_workspace, market_asset, 1, 100, date(2026, 3, 1))
    split = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.kind == "split")

    with pytest.raises(HTTPException) as exc:
        await asset_transaction_service.update_transaction(
            session, split.id, test_workspace.id, AssetTransactionUpdate(date=date(2026, 1, 1)),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == (
        "No units are held before 2026-01-01. Record the buys before the split first."
    )
    assert not session.dirty
    after = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.id == split.id)
    assert after.date == date(2026, 3, 1)


@pytest.mark.asyncio
async def test_moving_a_split_into_the_future_is_refused(session, test_workspace, market_asset):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 1, 1))
    await _add_split(session, test_workspace, market_asset, 1, 100, date(2026, 2, 1))
    split = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.kind == "split")

    with pytest.raises(HTTPException) as exc:
        await asset_transaction_service.update_transaction(
            session,
            split.id,
            test_workspace.id,
            AssetTransactionUpdate(date=app_today() + timedelta(days=1)),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == _FUTURE_SPLIT
    assert not session.dirty
    after = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.id == split.id)
    assert after.date == date(2026, 2, 1)


@pytest.mark.parametrize(
    "target_kind,patch,detail",
    [
        ("split", {"quantity": Decimal("5")}, _NO_MONEY),
        ("split", {"price": Decimal("5")}, _NO_MONEY),
        ("split", {"fee": Decimal("5")}, _NO_MONEY),
        ("split", {"old_units": 4, "new_units": 4}, "old_units and new_units must differ"),
        ("split", {"old_units": 0}, _RATIO_RANGE),
        ("split", {"new_units": None}, _RATIO_RANGE),
        ("buy", {"old_units": 1}, "old_units and new_units only apply to a split"),
        ("buy", {"quantity": Decimal("0")}, "quantity must be > 0"),
        ("buy", {"quantity": None}, "quantity must be > 0"),
        ("buy", {"price": Decimal("-1")}, "price must be >= 0"),
        ("buy", {"kind": "gift"}, "kind must be 'buy', 'sell' or 'split'"),
    ],
)
@pytest.mark.asyncio
async def test_editing_validates_before_touching_the_row(
    session, test_workspace, market_asset, target_kind, patch, detail
):
    await _buy(session, test_workspace, market_asset, "10", "20", date(2026, 1, 1))
    await _add_split(session, test_workspace, market_asset, 1, 2, date(2026, 2, 1))
    target = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.kind == target_kind)

    with pytest.raises(HTTPException) as exc:
        await asset_transaction_service.update_transaction(
            session, target.id, test_workspace.id, AssetTransactionUpdate(**patch),
        )

    assert exc.value.status_code == 422
    assert exc.value.detail == detail
    # The rejected edit must not be left pending for a later commit to persist.
    assert not session.dirty
    after = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.id == target.id)
    assert (after.quantity, after.price, after.old_units, after.new_units) == (
        target.quantity, target.price, target.old_units, target.new_units,
    )


@pytest.mark.asyncio
async def test_deleting_a_split_gives_back_the_previous_position(session, test_workspace, market_asset):
    before = await _buy(session, test_workspace, market_asset, "10", "480", date(2026, 1, 1))
    await _add_split(session, test_workspace, market_asset, 1, 100, date(2026, 2, 1))
    split = next(tx for tx in await _ledger(session, test_workspace, market_asset) if tx.kind == "split")

    read = await asset_transaction_service.delete_transaction(session, split.id, test_workspace.id)

    assert before is not None and read is not None
    assert read.units == before.units == 10
    assert read.average_price == pytest.approx(480.0)
    assert read.total_invested == pytest.approx(4800)
    assert read.transaction_count == 1


# Issue #944: a split is a ledger entry, and the chart values a day as the
# quantity held then times the price stored for that day. That only adds up
# while the stored prices are the ones quoted at the time, so no path may
# backfill history from a provider series (it would arrive adjusted for splits
# and break the product). Today's quote is the only price any path stores; past
# trades keep their own price in the ledger.


async def _stored_price_dates(session: AsyncSession, ticker: str) -> set[date]:
    rows = await session.execute(
        select(AssetValue.date)
        .join(Asset, Asset.id == AssetValue.asset_id)
        .where(Asset.ticker == ticker, AssetValue.price.is_not(None))
    )
    return set(rows.scalars().all())


@pytest.mark.asyncio
async def test_buying_into_a_holding_does_not_backfill_price_history(
    session, test_workspace, test_user
):
    provider = _FakeProvider({"ITUB4.SA": _quote("ITUB4.SA", 30.0)})
    await asset_transaction_service.buy_into_holding(
        session, test_workspace.id, test_user.id,
        AssetBuyCreate(
            ticker="ITUB4.SA", quantity=Decimal("10"), price=Decimal("28"),
            date=app_today() - timedelta(days=200),
        ),
        market_provider=provider,
    )
    assert await _stored_price_dates(session, "ITUB4.SA") == {app_today()}


@pytest.mark.asyncio
async def test_importing_old_orders_does_not_backfill_price_history(
    session, test_workspace, test_user
):
    orders, errors, _ = asset_import_service.parse_orders_csv(
        "\n".join([
            "ticker,date,quantity,price",
            f"ITUB4.SA,{(app_today() - timedelta(days=200)).isoformat()},10,28.00",
            f"ITUB4.SA,{(app_today() - timedelta(days=100)).isoformat()},-4,31.00",
        ]).encode()
    )
    assert errors == []
    summary = await asset_import_service.import_orders(
        session, test_workspace.id, test_user.id, orders,
        market_provider=_FakeProvider({"ITUB4.SA": _quote("ITUB4.SA", 30.0)}),
    )
    assert summary["imported"] == 2
    assert await _stored_price_dates(session, "ITUB4.SA") == {app_today()}


@pytest.mark.asyncio
async def test_creating_a_holding_with_a_past_purchase_does_not_backfill_price_history(
    session, test_workspace, test_user
):
    await asset_service.create_asset(
        session, test_workspace.id, test_user.id,
        AssetCreate(
            name="Itau", type="stock", valuation_method="market_price", ticker="ITUB4.SA",
            units=Decimal("10"), unit_price=Decimal("28"),
            purchase_date=app_today() - timedelta(days=200),
        ),
        market_provider=_FakeProvider({"ITUB4.SA": _quote("ITUB4.SA", 30.0)}),
    )
    assert await _stored_price_dates(session, "ITUB4.SA") == {app_today()}
