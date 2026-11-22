"""Trades a provider reports for its holdings, mirrored into the asset ledger (#1461)."""
from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.core.app_clock import app_today
from app.plugins.provider import provider_name
from tests import test_provider_plugins as provider_plugins
from tests.test_code_plugins import PLUGIN
from tests.test_provider_plugins import _pipeline, _rows, payload_data

bank_connection = provider_plugins.bank_connection

SOURCE = provider_name(PLUGIN)


def _trade(external_id: str, kind: str, day: int, quantity: str, price: str = "100", fee: str = "0") -> dict[str, Any]:
    return {"external_id": external_id, "kind": kind, "date": f"2026-11-{day:02d}", "quantity": quantity,
            "price": price, "fee": fee}


def holding(quantity: str, trades: Any, **extra: Any) -> dict[str, Any]:
    data = payload_data()
    data["holdings"][0].update(quantity=quantity, trades=copy.deepcopy(trades), **extra)
    return data


async def _asset(session, connection) -> Asset:
    (asset,) = await _rows(session, Asset, connection_id=connection.id)
    await session.refresh(asset)
    return asset


async def _ledger(session, asset) -> dict[str, tuple[str, Decimal, Decimal, str]]:
    rows = await _rows(session, AssetTransaction, asset_id=asset.id)
    ids = [tx.external_id or "" for tx in rows]
    assert len(ids) == len(set(ids)), f"duplicate trades in the ledger: {sorted(ids)}"
    return {
        tx.external_id or "": (tx.kind, Decimal(str(tx.quantity)).normalize(), Decimal(str(tx.price)).normalize(), tx.source)
        for tx in rows
    }


@pytest.mark.asyncio
async def test_trades_that_add_up_fill_the_ledger(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10", "98.5", "1")]), test_user)
    asset = await _asset(session, bank_connection)
    assert await _ledger(session, asset) == {"b1": ("buy", Decimal("10"), Decimal("98.5"), SOURCE)}
    assert asset.units == Decimal("10")
    assert Decimal(str(asset.average_price)) == Decimal("98.6")
    assert asset.purchase_date == date(2026, 11, 2)


@pytest.mark.asyncio
async def test_later_trades_join_the_stored_ones(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10", "100")]), test_user)
    await _pipeline(session, bank_connection, holding("6", [_trade("s1", "sell", 9, "4", "110")]), test_user)
    asset = await _asset(session, bank_connection)
    assert set(await _ledger(session, asset)) == {"b1", "s1"}
    assert asset.units == Decimal("6")
    assert Decimal(str(asset.realized_gain)) == Decimal("40.00")


@pytest.mark.asyncio
async def test_trades_that_do_not_add_up_leave_the_ledger(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10")]), test_user)
    await _pipeline(session, bank_connection, holding("12", [_trade("b1", "buy", 2, "10"), _trade("s1", "sell", 3, "5")]), test_user)
    asset = await _asset(session, bank_connection)
    assert set(await _ledger(session, asset)) == {"b1"}
    assert asset.units == Decimal("12")


@pytest.mark.asyncio
async def test_a_set_that_goes_below_zero_is_refused(session: AsyncSession, bank_connection, test_user):
    trades = [_trade("s1", "sell", 2, "5"), _trade("b1", "buy", 3, "5")]
    await _pipeline(session, bank_connection, holding("0", trades), test_user)
    asset = await _asset(session, bank_connection)
    assert await _ledger(session, asset) == {}


@pytest.mark.asyncio
async def test_a_reported_trade_corrects_the_stored_one(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10", "100")]), test_user)
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10", "101")]), test_user)
    asset = await _asset(session, bank_connection)
    assert await _ledger(session, asset) == {"b1": ("buy", Decimal("10"), Decimal("101"), SOURCE)}


@pytest.mark.asyncio
async def test_trades_entered_by_hand_are_never_touched(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("10", None), test_user)
    asset = await _asset(session, bank_connection)
    session.add(AssetTransaction(asset_id=asset.id, workspace_id=asset.workspace_id, kind="buy",
                                 quantity=Decimal("3"), price=Decimal("50"), fee=Decimal("0"),
                                 date=date(2026, 10, 1), source="manual"))
    await session.commit()
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10")]), test_user)
    asset = await _asset(session, bank_connection)
    ledger = await _ledger(session, asset)
    assert ledger[""] == ("buy", Decimal("3"), Decimal("50"), "manual") and "b1" in ledger
    assert asset.units == Decimal("10")


@pytest.mark.asyncio
async def test_trades_not_reported_leave_the_ledger(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10")]), test_user)
    await _pipeline(session, bank_connection, holding("10", None), test_user)
    asset = await _asset(session, bank_connection)
    assert set(await _ledger(session, asset)) == {"b1"}


@pytest.mark.asyncio
async def test_a_closed_position_realizes_its_gain(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10", "100")]), test_user)
    closed = holding("0", [_trade("s1", "sell", 9, "10", "120")], is_withdrawn=True)
    await _pipeline(session, bank_connection, closed, test_user)
    asset = await _asset(session, bank_connection)
    assert set(await _ledger(session, asset)) == {"b1", "s1"}
    assert Decimal(str(asset.realized_gain)) == Decimal("200.00")
    assert asset.sell_date == date(2026, 11, 9)


@pytest.mark.asyncio
async def test_a_holding_names_its_type_and_a_type_set_by_hand_stays(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("10", None, type="etf"), test_user)
    asset = await _asset(session, bank_connection)
    assert asset.type == "etf"
    asset.type = "fund"  # changed by hand
    await session.commit()
    await _pipeline(session, bank_connection, holding("10", None, type="stock"), test_user)
    assert (await _asset(session, bank_connection)).type == "fund"


@pytest.mark.asyncio
async def test_a_holding_without_a_type_is_an_investment_until_one_is_named(
    session: AsyncSession, bank_connection, test_user
):
    await _pipeline(session, bank_connection, holding("10", None), test_user)
    assert (await _asset(session, bank_connection)).type == "investment"
    await _pipeline(session, bank_connection, holding("10", None, type="etf"), test_user)
    assert (await _asset(session, bank_connection)).type == "etf"


@pytest.mark.asyncio
async def test_a_sale_the_user_recorded_stays_when_trades_are_written(
    session: AsyncSession, bank_connection, test_user
):
    await _pipeline(session, bank_connection, holding("10", None), test_user)
    asset = await _asset(session, bank_connection)
    asset.sell_date, asset.sell_price = date(2026, 11, 1), Decimal("1200")  # sold, as the user records it
    await session.commit()
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10")]), test_user)
    asset = await _asset(session, bank_connection)
    assert "b1" in await _ledger(session, asset)
    assert (asset.sell_date, asset.sell_price) == (date(2026, 11, 1), Decimal("1200"))



@pytest.mark.asyncio
async def test_a_position_sold_out_and_bought_again_is_held_again(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10")]), test_user)
    # Sold out, with the position still listed at zero: the ledger marks it sold.
    await _pipeline(session, bank_connection, holding("0", [_trade("s1", "sell", 9, "10", "120")]), test_user)
    assert (await _asset(session, bank_connection)).sell_date == date(2026, 11, 9)
    await _pipeline(session, bank_connection, holding("5", [_trade("b2", "buy", 12, "5", "110")]), test_user)
    asset = await _asset(session, bank_connection)
    assert set(await _ledger(session, asset)) == {"b1", "s1", "b2"}
    assert (asset.units, asset.sell_date) == (Decimal("5"), None)


@pytest.mark.asyncio
async def test_reported_trades_that_add_up_alone_replace_a_ledger_they_do_not_join(
    session: AsyncSession, bank_connection, test_user
):
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10")]), test_user)
    # The provider restated its history under new ids: with the stored buy it
    # would be 15 units, on its own it is the 5 reported.
    await _pipeline(session, bank_connection, holding("5", [_trade("r1", "buy", 3, "5", "90")]), test_user)
    asset = await _asset(session, bank_connection)
    assert await _ledger(session, asset) == {"r1": ("buy", Decimal("5"), Decimal("90"), SOURCE)}
    assert asset.units == Decimal("5")


@pytest.mark.asyncio
async def test_a_market_priced_asset_keeps_its_own_trades(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("10", None), test_user)
    asset = await _asset(session, bank_connection)
    asset.valuation_method = "market_price"  # the user tracks it by its quote and their own buys
    await session.commit()
    await _pipeline(session, bank_connection, holding("10", [_trade("b1", "buy", 2, "10")]), test_user)
    assert await _ledger(session, await _asset(session, bank_connection)) == {}


@pytest.mark.asyncio
async def test_a_position_closed_before_the_first_sync_comes_in_sold(
    session: AsyncSession, bank_connection, test_user
):
    trades = [_trade("b1", "buy", 2, "10", "100"), _trade("s1", "sell", 9, "10", "120")]
    data = holding("0", trades, is_withdrawn=True, current_value="0")
    await _pipeline(session, bank_connection, data, test_user)
    asset = await _asset(session, bank_connection)
    assert asset.sell_date == date(2026, 11, 9)
    assert Decimal(str(asset.realized_gain)) == Decimal("200.00")
    assert set(await _ledger(session, asset)) == {"b1", "s1"}


@pytest.mark.asyncio
async def test_a_closed_position_without_trades_is_still_skipped(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("0", None, is_withdrawn=True, current_value="0"), test_user)
    assert await _rows(session, Asset, connection_id=bank_connection.id) == []


@pytest.mark.asyncio
async def test_a_buy_corrected_onto_the_day_of_its_sale_replays_before_it(
    session: AsyncSession, bank_connection, test_user
):
    closed = {"is_withdrawn": True, "current_value": "0"}
    first = [_trade("b1", "buy", 2, "10", "100"), _trade("s1", "sell", 9, "10", "120")]
    await _pipeline(session, bank_connection, holding("0", first, **closed), test_user)
    # The provider corrects the buy: another id, the same day as the sale.
    corrected = [_trade("b2", "buy", 9, "10", "100"), _trade("s1", "sell", 9, "10", "120")]
    await _pipeline(session, bank_connection, holding("0", corrected, **closed), test_user)
    asset = await _asset(session, bank_connection)
    assert set(await _ledger(session, asset)) == {"b2", "s1"}
    assert asset.sell_date == date(2026, 11, 9)
    assert Decimal(str(asset.realized_gain)) == Decimal("200.00")
    assert asset.purchase_price is None and Decimal(str(asset.units)) == Decimal("0")


@pytest.mark.asyncio
async def test_a_position_bought_again_after_a_sale_is_open(session: AsyncSession, bank_connection, test_user):
    bought = [_trade("b1", "buy", 2, "10", "100")]
    await _pipeline(session, bank_connection, holding("10", bought), test_user)
    sold = bought + [_trade("s1", "sell", 9, "10", "120")]
    await _pipeline(session, bank_connection, holding("0", sold, is_withdrawn=True, current_value="0"), test_user)
    asset = await _asset(session, bank_connection)
    assert asset.sell_date == date(2026, 11, 9)
    again = sold + [_trade("b2", "buy", 12, "5", "110")]
    await _pipeline(session, bank_connection, holding("5", again, current_value="560.00"), test_user)
    asset = await _asset(session, bank_connection)
    assert asset.sell_date is None and Decimal(str(asset.units)) == Decimal("5")
    # Its value is today's again, not the one it had before the sale.
    (today_value,) = [v for v in await _rows(session, AssetValue, asset_id=asset.id) if v.date == app_today()]
    assert Decimal(str(today_value.amount)) == Decimal("560.00")


async def _chart(session, asset) -> dict[date, float]:
    from app.services.asset_service import _load_asset_native_values

    return dict((await _load_asset_native_values(session, [asset]))[str(asset.id)])


@pytest.mark.asyncio
async def test_holding_with_provider_trades_is_drawn_from_its_ledger(session: AsyncSession, bank_connection, test_user):
    trades = [_trade("b1", "buy", 2, "2", "50"), _trade("b2", "buy", 5, "1", "60")]
    await _pipeline(session, bank_connection, holding("3", trades, unit_price="70", current_value="210"), test_user)
    asset = await _asset(session, bank_connection)
    chart = await _chart(session, asset)
    assert chart[date(2026, 11, 2)] == 100.0
    assert chart[date(2026, 11, 5)] == 180.0
    assert chart[app_today()] == 210.0


@pytest.mark.asyncio
async def test_holding_without_provider_ledger_keeps_stored_values(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("3", None, unit_price="70", current_value="210"), test_user)
    asset = await _asset(session, bank_connection)
    assert await _chart(session, asset) == {app_today(): 210.0}


@pytest.mark.asyncio
async def test_hand_trades_alone_do_not_switch_a_synced_holding(session: AsyncSession, bank_connection, test_user):
    await _pipeline(session, bank_connection, holding("3", None, unit_price="70", current_value="210"), test_user)
    asset = await _asset(session, bank_connection)
    session.add(AssetTransaction(asset_id=asset.id, workspace_id=asset.workspace_id, kind="buy", quantity=Decimal("3"),
                                 price=Decimal("10"), fee=Decimal("0"), date=date(2026, 11, 1), source="manual"))
    await session.commit()
    assert await _chart(session, asset) == {app_today(): 210.0}


@pytest.mark.asyncio
async def test_a_ledger_that_no_longer_adds_up_falls_back_to_stored_values(
    session: AsyncSession, bank_connection, test_user
):
    trades = [_trade("b1", "buy", 2, "2", "50"), _trade("b2", "buy", 5, "1", "60")]
    await _pipeline(session, bank_connection, holding("3", trades, unit_price="70", current_value="210"), test_user)
    # A later sync whose trades don't reach the reported units keeps the old ledger.
    await _pipeline(session, bank_connection, holding("6", trades, unit_price="70", current_value="420"), test_user)
    asset = await _asset(session, bank_connection)
    chart = await _chart(session, asset)
    assert date(2026, 11, 5) not in chart  # no replayed trade point
    assert chart[app_today()] == 420.0


@pytest.mark.asyncio
async def test_hand_trades_are_not_replayed_with_provider_trades(session: AsyncSession, bank_connection, test_user):
    trades = [_trade("b1", "buy", 2, "2", "50"), _trade("b2", "buy", 5, "1", "60")]
    await _pipeline(session, bank_connection, holding("3", trades, unit_price="70", current_value="210"), test_user)
    asset = await _asset(session, bank_connection)
    session.add(AssetTransaction(asset_id=asset.id, workspace_id=asset.workspace_id, kind="buy", quantity=Decimal("3"),
                                 price=Decimal("10"), fee=Decimal("0"), date=date(2026, 11, 1), source="manual"))
    await session.commit()
    chart = await _chart(session, asset)
    assert date(2026, 11, 1) not in chart
    assert (chart[date(2026, 11, 2)], chart[date(2026, 11, 5)], chart[app_today()]) == (100.0, 180.0, 210.0)


@pytest.mark.asyncio
async def test_a_ledger_the_import_accepted_is_replayed_within_its_largest_lot(
    session: AsyncSession, bank_connection, test_user
):
    # Buys of 1000 and a sale of 999.999 leave 0.001 against 0.0015 reported:
    # within the import's tolerance (one millionth of the largest lot), so the
    # chart replays the ledger too instead of falling back to stored values.
    trades = [_trade("b1", "buy", 2, "1000", "1"), _trade("s1", "sell", 5, "999.999", "1")]
    await _pipeline(session, bank_connection, holding("0.0015", trades, unit_price="1", current_value="0.0015"), test_user)
    asset = await _asset(session, bank_connection)
    assert len(await _ledger(session, asset)) == 2  # the import accepted it
    chart = await _chart(session, asset)
    assert chart[date(2026, 11, 2)] == 1000.0


@pytest.mark.asyncio
async def test_a_sync_point_keeps_the_amount_the_provider_reported(session: AsyncSession, bank_connection, test_user):
    # A provider may report a holding's value apart from its quote (Pluggy:
    # the net balance, against the unit price): 100 units at 9.50 reported as
    # 1000.00. The sync day shows what was reported, so the chart's last point
    # equals the holding's current value; the trade day in between is replayed.
    trades = [_trade("b1", "buy", 2, "100", "9")]
    await _pipeline(session, bank_connection, holding("100", trades, unit_price="9.50", current_value="1000.00"), test_user)
    asset = await _asset(session, bank_connection)
    chart = await _chart(session, asset)
    assert chart[date(2026, 11, 2)] == 900.0
    assert chart[app_today()] == 1000.0

    from app.services.asset_service import get_asset_value_trend

    points = await get_asset_value_trend(session, asset.id, asset.workspace_id)
    assert points is not None
    trend = {p["date"]: p["amount"] for p in points}
    assert trend[app_today().isoformat()] == 1000.0
