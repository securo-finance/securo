"""Edge cases for the time-weighted portfolio return.

Every scenario values a holding from its ledger and stored prices, exactly as
the Assets page does, and checks the result against a return worked out by
hand: each interval's growth is the end value plus anything withdrawn, over
the start value plus anything contributed.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.models.user import User
from app.services.portfolio_performance_service import get_portfolio_performance_multi

END = date.today()
D0 = END - timedelta(days=20)


def day(offset: int) -> date:
    return D0 + timedelta(days=offset)


async def stock(session: AsyncSession, user: User, workspace, name: str) -> Asset:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name=name,
        type="stock",
        currency="BRL",
        valuation_method="market_price",
        ticker=name,
    )
    session.add(asset)
    await session.flush()
    return asset


def trade(asset: Asset, kind: str, when: date, quantity: str, price: str, fee: str = "0"):
    return AssetTransaction(
        asset_id=asset.id,
        workspace_id=asset.workspace_id,
        kind=kind,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fee=Decimal(fee),
        date=when,
    )


def close(asset: Asset, when: date, price: str, quantity: str = "10"):
    """A stored daily close; the amount is rebuilt from the ledger anyway."""
    return AssetValue(
        asset_id=asset.id,
        workspace_id=asset.workspace_id,
        amount=Decimal(price) * Decimal(quantity),
        price=Decimal(price),
        date=when,
        source="market",
    )


async def portfolio_return(session: AsyncSession, user: User, workspace) -> float:
    await session.commit()
    result = await get_portfolio_performance_multi(
        session, workspace.id, user.id, "BRL", [], "3m", end_date=END
    )
    assert result.portfolio_return is not None
    return result.portfolio_return


@pytest.mark.asyncio
async def test_selling_everything_above_the_last_close_keeps_the_gain(
    session: AsyncSession, test_user: User, test_workspace
):
    held = await stock(session, test_user, test_workspace, "GAIN3")
    other = await stock(session, test_user, test_workspace, "FLAT3")
    session.add_all(
        [
            trade(held, "buy", day(0), "10", "100"),
            trade(other, "buy", day(0), "10", "100"),
            close(held, day(0), "100"),
            close(other, day(0), "100"),
            close(held, day(1), "110"),
            # Sold intraday at 120, above the last stored close of 110.
            trade(held, "sell", day(2), "10", "120"),
        ]
    )
    # 2000 → 2100 at the close, then the sale realises 1200 for that half.
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_selling_everything_below_the_last_close_is_not_a_total_loss(
    session: AsyncSession, test_user: User, test_workspace
):
    held = await stock(session, test_user, test_workspace, "LOSS3")
    session.add_all(
        [
            trade(held, "buy", day(0), "10", "100"),
            close(held, day(0), "100"),
            close(held, day(1), "110"),
            trade(held, "sell", day(2), "10", "99"),
        ]
    )
    # Bought for 1000, sold for 990: down 1%, not 100%.
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(-1.0)


@pytest.mark.asyncio
async def test_partial_sale_keeps_measuring_what_is_left(
    session: AsyncSession, test_user: User, test_workspace
):
    held = await stock(session, test_user, test_workspace, "HALF3")
    session.add_all(
        [
            trade(held, "buy", day(0), "10", "100"),
            close(held, day(0), "100"),
            close(held, day(1), "110"),
            trade(held, "sell", day(1), "5", "110"),
            close(held, day(2), "121", "5"),
        ]
    )
    # +10% on ten shares, then +10% on the five that remain.
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(21.0)


@pytest.mark.asyncio
async def test_selling_and_buying_back_lower_compounds_both_legs(
    session: AsyncSession, test_user: User, test_workspace
):
    held = await stock(session, test_user, test_workspace, "BACK3")
    session.add_all(
        [
            trade(held, "buy", day(0), "10", "100"),
            close(held, day(0), "100"),
            close(held, day(1), "80"),
            trade(held, "sell", day(1), "10", "80"),
            trade(held, "buy", day(2), "10", "50"),
            close(held, day(2), "50"),
            close(held, day(3), "60"),
        ]
    )
    # -20% while held the first time, nothing while out, +20% the second time.
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(-4.0)


@pytest.mark.asyncio
async def test_bankruptcy_wipes_out_only_that_holding(
    session: AsyncSession, test_user: User, test_workspace
):
    bust = await stock(session, test_user, test_workspace, "BUST3")
    sound = await stock(session, test_user, test_workspace, "SOUND3")
    session.add_all(
        [
            trade(bust, "buy", day(0), "10", "100"),
            trade(sound, "buy", day(0), "10", "100"),
            close(bust, day(0), "100"),
            close(sound, day(0), "100"),
            close(bust, day(1), "0"),
            close(sound, day(1), "100"),
            close(sound, day(2), "110"),
        ]
    )
    # Half the portfolio goes to zero (-50%), the rest then gains 10%.
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(-45.0)


@pytest.mark.asyncio
async def test_writing_off_a_delisted_holding_by_selling_at_zero(
    session: AsyncSession, test_user: User, test_workspace
):
    """A delisted ticker keeps its last close forever until it is written off."""
    gone = await stock(session, test_user, test_workspace, "GONE3")
    sound = await stock(session, test_user, test_workspace, "KEEP3")
    session.add_all(
        [
            trade(gone, "buy", day(0), "10", "100"),
            trade(sound, "buy", day(0), "10", "100"),
            close(gone, day(0), "100"),
            close(sound, day(0), "100"),
            close(sound, day(5), "100"),
            trade(gone, "sell", day(5), "10", "0"),
        ]
    )
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(-50.0)


@pytest.mark.asyncio
async def test_day_trade_profit_counts_even_when_netted_on_one_date(
    session: AsyncSession, test_user: User, test_workspace
):
    core = await stock(session, test_user, test_workspace, "CORE3")
    flip = await stock(session, test_user, test_workspace, "FLIP3")
    session.add_all(
        [
            trade(core, "buy", day(0), "10", "100"),
            close(core, day(0), "100"),
            close(core, day(1), "100"),
            trade(flip, "buy", day(1), "10", "100", fee="1"),
            trade(flip, "sell", day(1), "10", "105", fee="1"),
        ]
    )
    # 1001 in and 1049 out on the same day, alongside 1000 that did nothing.
    expected = ((1000 + 1049) / (1000 + 1001) - 1) * 100
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(expected)


async def sold_manual_asset(
    session: AsyncSession, user: User, workspace, sell_price: str | None
) -> None:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Apartment",
        type="real_estate",
        currency="BRL",
        valuation_method="manual",
        purchase_date=day(0),
        purchase_price=Decimal("100"),
        sell_date=day(15),
        sell_price=Decimal(sell_price) if sell_price is not None else None,
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=asset.id,
                workspace_id=workspace.id,
                amount=Decimal(amount),
                date=when,
                source="manual",
            )
            for when, amount in ((day(0), "100"), (day(10), "120"))
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("sell_price", "expected"), [("130", 30.0), ("100", 0.0)])
async def test_manual_asset_return_ends_at_its_sale_price(
    session: AsyncSession, test_user: User, test_workspace, sell_price, expected
):
    await sold_manual_asset(session, test_user, test_workspace, sell_price)
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(expected)


@pytest.mark.asyncio
async def test_manual_asset_sold_without_a_price_ends_at_its_last_value(
    session: AsyncSession, test_user: User, test_workspace
):
    """The sale price was left blank, which is not a total loss."""
    await sold_manual_asset(session, test_user, test_workspace, None)
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_provider_redemption_without_trades_pays_out_the_last_balance(
    session: AsyncSession, test_user: User, test_workspace
):
    """Pluggy reports a CDB fully redeemed, with no trades and no sale price."""
    held = await stock(session, test_user, test_workspace, "HOLD3")
    cdb = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="CDB",
        type="investment",
        currency="BRL",
        valuation_method="manual",
        source="pluggy",
        external_metadata={"type": "FIXED_INCOME", "status": "TOTAL_WITHDRAWAL"},
        sell_date=day(5),
    )
    session.add(cdb)
    await session.flush()
    session.add_all(
        [
            trade(held, "buy", day(0), "10", "100"),
            close(held, day(0), "100"),
            close(held, day(10), "100"),
            *(
                AssetValue(
                    asset_id=cdb.id,
                    workspace_id=test_workspace.id,
                    amount=Decimal(amount),
                    date=when,
                    source="pluggy",
                )
                for when, amount in ((day(0), "1000"), (day(5), "1010"))
            ),
        ]
    )
    # The CDB earned 10 on a 2000 portfolio, then paid out its last balance.
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_split_recorded_as_free_shares_is_not_a_loss(
    session: AsyncSession, test_user: User, test_workspace
):
    held = await stock(session, test_user, test_workspace, "SPLT3")
    session.add_all(
        [
            trade(held, "buy", day(0), "10", "100"),
            close(held, day(0), "100"),
            # 2-for-1 split: the close halves and ten more shares arrive free.
            trade(held, "buy", day(1), "10", "0"),
            close(held, day(1), "50", "20"),
            close(held, day(2), "55", "20"),
        ]
    )
    assert await portfolio_return(session, test_user, test_workspace) == pytest.approx(10.0)
