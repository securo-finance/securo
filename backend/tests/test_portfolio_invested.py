"""The invested-capital line: money put into the charted assets, by date.

Each scenario is worked out by hand from the ledger (buy = quantity x price
+ fee, sell = -(quantity x price - fee)) and compared with the cumulative
points the service returns.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.models.fx_rate import FxRate
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.core.app_clock import app_today
from app.services.portfolio_invested_service import get_invested_capital

D1 = date(2026, 3, 2)


def day(offset: int) -> date:
    return D1 + timedelta(days=offset)


async def use_currency(session: AsyncSession, user: User, currency: str = "EUR") -> None:
    user.preferences = {**(user.preferences or {}), "currency_display": currency}
    session.add(user)
    await session.commit()


async def holding(
    session: AsyncSession,
    user: User,
    workspace,
    name: str,
    *,
    currency: str = "EUR",
    group: AssetGroup | None = None,
    **fields,
) -> Asset:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name=name,
        type="stock",
        currency=currency,
        valuation_method=fields.pop("valuation_method", "market_price"),
        ticker=name,
        group_id=group.id if group else None,
        **fields,
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
    return AssetValue(
        asset_id=asset.id,
        workspace_id=asset.workspace_id,
        amount=Decimal(price) * Decimal(quantity),
        price=Decimal(price),
        date=when,
        source="market",
    )


def manual_value(asset: Asset, when: date, amount: str):
    return AssetValue(
        asset_id=asset.id,
        workspace_id=asset.workspace_id,
        amount=Decimal(amount),
        date=when,
        source="manual",
    )


def as_pairs(points: list[dict]) -> list[tuple[date, float]]:
    return [(point["date"], point["amount"]) for point in points]


@pytest.mark.asyncio
async def test_buys_and_sales_with_fees(session: AsyncSession, test_user: User, test_workspace):
    await use_currency(session, test_user)
    asset = await holding(session, test_user, test_workspace, "ABC")
    session.add_all(
        [
            trade(asset, "buy", day(0), "10", "100", "1"),
            trade(asset, "buy", day(1), "5", "110", "1"),
            trade(asset, "sell", day(2), "8", "120", "2"),
            close(asset, day(0), "100"),
            close(asset, day(1), "110"),
            close(asset, day(2), "120"),
        ]
    )
    await session.commit()

    points = await get_invested_capital(session, test_workspace.id, test_user.id, end_date=day(5))

    # 1000 + 1; + 550 + 1; - (960 - 2).
    assert as_pairs(points) == [(day(0), 1001.0), (day(1), 1552.0), (day(2), 594.0)]


@pytest.mark.asyncio
async def test_each_trade_uses_its_own_days_exchange_rate(
    session: AsyncSession, test_user: User, test_workspace
):
    await use_currency(session, test_user)
    asset = await holding(session, test_user, test_workspace, "USDX", currency="USD")
    session.add_all(
        [
            FxRate(
                base_currency="USD",
                quote_currency="EUR",
                date=day(0),
                rate=Decimal("0.90"),
                source="test",
            ),
            FxRate(
                base_currency="USD",
                quote_currency="EUR",
                date=day(1),
                rate=Decimal("0.95"),
                source="test",
            ),
            trade(asset, "buy", day(0), "10", "100"),
            trade(asset, "sell", day(1), "5", "120"),
            close(asset, day(0), "100"),
            close(asset, day(1), "120", "5"),
        ]
    )
    await session.commit()

    points = await get_invested_capital(session, test_workspace.id, test_user.id, end_date=day(5))

    # 1000 USD x 0.90 = 900; then 600 USD x 0.95 = 570 comes back.
    assert as_pairs(points) == [(day(0), 900.0), (day(1), 330.0)]


@pytest.mark.asyncio
async def test_split_moves_no_money(session: AsyncSession, test_user: User, test_workspace):
    await use_currency(session, test_user)
    asset = await holding(session, test_user, test_workspace, "SPLT")
    session.add_all(
        [
            trade(asset, "buy", day(0), "10", "100"),
            AssetTransaction(
                asset_id=asset.id,
                workspace_id=asset.workspace_id,
                kind="split",
                quantity=Decimal("0"),
                price=Decimal("0"),
                fee=Decimal("0"),
                old_units=1,
                new_units=2,
                date=day(1),
            ),
            trade(asset, "sell", day(2), "20", "60"),
            close(asset, day(0), "100"),
            close(asset, day(1), "50", "20"),
            close(asset, day(2), "60", "20"),
        ]
    )
    await session.commit()

    points = await get_invested_capital(session, test_workspace.id, test_user.id, end_date=day(5))

    assert as_pairs(points) == [(day(0), 1000.0), (day(2), -200.0)]


@pytest.mark.asyncio
async def test_manual_asset_with_cost_and_sale(
    session: AsyncSession, test_user: User, test_workspace
):
    await use_currency(session, test_user)
    asset = await holding(
        session,
        test_user,
        test_workspace,
        "House",
        valuation_method="manual",
        purchase_date=day(0),
        purchase_price=Decimal("5000"),
        sell_date=day(5),
        sell_price=Decimal("6500"),
    )
    session.add_all([manual_value(asset, day(0), "5000"), manual_value(asset, day(4), "6000")])
    await session.commit()

    points = await get_invested_capital(session, test_workspace.id, test_user.id, end_date=day(10))

    assert as_pairs(points) == [(day(0), 5000.0), (day(6), -1500.0)]


@pytest.mark.asyncio
async def test_manual_asset_without_cost_counts_its_first_value(
    session: AsyncSession, test_user: User, test_workspace
):
    await use_currency(session, test_user)
    asset = await holding(session, test_user, test_workspace, "Fund", valuation_method="manual")
    session.add_all([manual_value(asset, day(0), "2000"), manual_value(asset, day(3), "2500")])
    await session.commit()

    points = await get_invested_capital(session, test_workspace.id, test_user.id, end_date=day(10))

    assert as_pairs(points) == [(day(0), 2000.0)]


async def two_wallets(session: AsyncSession, user: User, workspace):
    groups = []
    for name, price in (("W1", "100"), ("W2", "30")):
        group = AssetGroup(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name=name)
        session.add(group)
        await session.flush()
        asset = await holding(session, user, workspace, name, group=group)
        session.add_all([trade(asset, "buy", day(0), "10", price), close(asset, day(0), price)])
        groups.append(group)
    await session.commit()
    return groups


@pytest.mark.asyncio
async def test_collection_filter_limits_the_assets(
    session: AsyncSession, test_user: User, test_workspace
):
    await use_currency(session, test_user)
    w1, _ = await two_wallets(session, test_user, test_workspace)

    only_w1 = await get_invested_capital(
        session, test_workspace.id, test_user.id, [w1.id], end_date=day(5)
    )
    everything = await get_invested_capital(
        session, test_workspace.id, test_user.id, end_date=day(5)
    )

    assert as_pairs(only_w1) == [(day(0), 1000.0)]
    assert as_pairs(everything) == [(day(0), 1300.0)]


@pytest.mark.asyncio
async def test_archived_asset_is_left_out(session: AsyncSession, test_user: User, test_workspace):
    await use_currency(session, test_user)
    asset = await holding(session, test_user, test_workspace, "OLD", is_archived=True)
    session.add_all([trade(asset, "buy", day(0), "10", "100"), close(asset, day(0), "100")])
    await session.commit()

    assert (
        await get_invested_capital(session, test_workspace.id, test_user.id, end_date=day(5)) == []
    )


@pytest.mark.asyncio
async def test_empty_workspace_has_no_points(
    session: AsyncSession, test_user: User, test_workspace
):
    assert await get_invested_capital(session, test_workspace.id, test_user.id) == []


@pytest.mark.asyncio
async def test_group_of_another_workspace_filters_out_everything(
    session: AsyncSession, test_user: User, test_workspace
):
    await use_currency(session, test_user)
    await two_wallets(session, test_user, test_workspace)
    other = Workspace(
        id=uuid.uuid4(),
        name="Other",
        kind="personal",
        created_by_user_id=test_user.id,
        default_currency="EUR",
    )
    session.add(other)
    await session.flush()
    session.add(
        WorkspaceMember(id=uuid.uuid4(), workspace_id=other.id, user_id=test_user.id, role="owner")
    )
    foreign_group = AssetGroup(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=other.id, name="X"
    )
    session.add(foreign_group)
    await session.flush()
    foreign = await holding(session, test_user, other, "FOREIGN", group=foreign_group)
    session.add_all([trade(foreign, "buy", day(0), "10", "100"), close(foreign, day(0), "100")])
    await session.commit()

    points = await get_invested_capital(
        session, test_workspace.id, test_user.id, [foreign_group.id], end_date=day(5)
    )
    own = await get_invested_capital(
        session, other.id, test_user.id, [foreign_group.id], end_date=day(5)
    )

    assert points == []
    # The same wallet does have points in its own workspace.
    assert as_pairs(own) == [(day(0), 1000.0)]


@pytest.mark.asyncio
async def test_trade_dated_after_today_is_counted_without_an_end_date(
    session: AsyncSession, test_user: User, test_workspace
):
    await use_currency(session, test_user)
    today = app_today()
    asset = await holding(session, test_user, test_workspace, "FUT")
    session.add_all(
        [
            trade(asset, "buy", today - timedelta(days=3), "10", "100"),
            trade(asset, "buy", today + timedelta(days=2), "5", "100"),
            close(asset, today - timedelta(days=3), "100"),
        ]
    )
    await session.commit()

    points = await get_invested_capital(session, test_workspace.id, test_user.id)

    assert as_pairs(points) == [
        (today - timedelta(days=3), 1000.0),
        (today + timedelta(days=2), 1500.0),
    ]


@pytest.mark.asyncio
async def test_cents_do_not_drift_when_only_the_total_is_rounded(
    session: AsyncSession, test_user: User, test_workspace
):
    await use_currency(session, test_user)
    asset = await holding(session, test_user, test_workspace, "CENT")
    for offset in range(3):
        session.add_all(
            [trade(asset, "buy", day(offset), "0.335", "3"), close(asset, day(offset), "3")]
        )
    await session.commit()

    points = await get_invested_capital(session, test_workspace.id, test_user.id, end_date=day(5))

    # 1.005, 2.010 and 3.015 exactly, rounded half up only at the total.
    assert as_pairs(points) == [(day(0), 1.0), (day(1), 2.01), (day(2), 3.02)]


async def synced(
    session: AsyncSession,
    user: User,
    workspace,
    name: str,
    metadata: dict,
    snapshots: list[tuple],
) -> Asset:
    """A provider-synced holding with no ledger: (day, amount, unit price[, gross]) snapshots."""
    asset = await holding(
        session,
        user,
        workspace,
        name,
        valuation_method="manual",
        source="pluggy",
        external_metadata=metadata,
    )
    session.add_all(
        AssetValue(
            asset_id=asset.id,
            workspace_id=workspace.id,
            amount=Decimal(snapshot[1]),
            price=Decimal(snapshot[2]) if snapshot[2] else None,
            gross_amount=Decimal(snapshot[3]) if len(snapshot) > 3 else None,
            date=day(snapshot[0]),
            source="sync",
        )
        for snapshot in snapshots
    )
    await session.commit()
    return asset


@pytest.mark.asyncio
async def test_synced_position_counts_the_change_in_shares(
    session: AsyncSession, test_user: User, test_workspace
):
    await use_currency(session, test_user)
    # 10 shares at 100; 15 at 110 (+5 x 110); 10 at 120 (-5 x 120).
    await synced(
        session,
        test_user,
        test_workspace,
        "POS",
        {"type": "EQUITY"},
        [(0, "1000", "100"), (1, "1650", "110"), (2, "1200", "120")],
    )

    points = await get_invested_capital(session, test_workspace.id, test_user.id, end_date=day(5))

    assert as_pairs(points) == [(day(0), 1000.0), (day(1), 1550.0), (day(2), 950.0)]


@pytest.mark.asyncio
async def test_synced_fixed_income_without_shares_reads_a_drop_as_a_redemption(
    session: AsyncSession, test_user: User, test_workspace
):
    await use_currency(session, test_user)
    await synced(
        session,
        test_user,
        test_workspace,
        "CDB",
        {"type": "FIXED_INCOME", "status": "ACTIVE"},
        # Pre-tax values differ, so reading them instead would redeem 507, not 510.
        [
            (0, "1000", None, "1000"),
            (1, "1010", None, "1012"),
            (2, "500", None, "505"),
            (3, "505", None, "510"),
        ],
    )

    points = await get_invested_capital(session, test_workspace.id, test_user.id, end_date=day(5))

    # 1000 in; the rise is yield; the 510 drop of the withdrawable value, as the
    # chart draws it, is a redemption; the rest is yield.
    assert as_pairs(points) == [(day(0), 1000.0), (day(2), 490.0)]


@pytest.mark.asyncio
async def test_manual_asset_sold_without_a_price_returns_its_last_value(
    session: AsyncSession, test_user: User, test_workspace
):
    await use_currency(session, test_user)
    asset = await holding(
        session, test_user, test_workspace, "Fund", valuation_method="manual", sell_date=day(5)
    )
    session.add_all([manual_value(asset, day(0), "2000"), manual_value(asset, day(3), "2500")])
    await session.commit()

    points = await get_invested_capital(session, test_workspace.id, test_user.id, end_date=day(10))

    assert as_pairs(points) == [(day(0), 2000.0), (day(6), -500.0)]


@pytest.mark.asyncio
async def test_route_returns_points_for_owner_and_viewer(
    client: AsyncClient,
    session: AsyncSession,
    test_user: User,
    test_workspace,
    auth_headers: dict,
    viewer_auth_headers: dict,
):
    await use_currency(session, test_user)
    w1, _ = await two_wallets(session, test_user, test_workspace)

    # The viewer reads in their own currency (the app default, USD): at
    # 0.5 EUR per USD the same 1300 EUR invested is 2600 USD.
    session.add(
        FxRate(
            base_currency="USD",
            quote_currency="EUR",
            date=day(0),
            rate=Decimal("0.5"),
            source="test",
        )
    )
    await session.commit()

    for headers, amount in ((auth_headers, 1300.0), (viewer_auth_headers, 2600.0)):
        response = await client.get("/api/assets/portfolio-invested", headers=headers)
        assert response.status_code == 200
        assert response.json() == [{"date": day(0).isoformat(), "amount": pytest.approx(amount)}]

    filtered = await client.get(
        "/api/assets/portfolio-invested",
        params={"asset_group_ids": [str(w1.id)]},
        headers=auth_headers,
    )
    assert filtered.status_code == 200
    assert filtered.json() == [{"date": day(0).isoformat(), "amount": 1000.0}]
