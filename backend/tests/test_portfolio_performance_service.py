from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.models.user import User
from app.providers.benchmark import BenchmarkHistoryPoint
from app.services.portfolio_performance_service import (
    _benchmark_index_returns,
    _cash_flows,
    _fill_aligned_series,
    get_portfolio_performance,
    get_portfolio_performance_multi,
    snapshot_ledger_flows,
    snapshot_position_flows,
    time_weighted_returns,
)


class FakeHistoryProvider:
    def __init__(self, points: list[BenchmarkHistoryPoint]) -> None:
        self.points = points
        self.calls = 0

    async def history(self, provider_name, symbol, start_date, end_date):
        self.calls += 1
        return self.points


class MultiHistoryProvider:
    def __init__(self, points: dict[str, list[BenchmarkHistoryPoint]]) -> None:
        self.points = points
        self.calls: list[str] = []

    async def history(self, provider_name, symbol, start_date, end_date):
        self.calls.append(symbol)
        return self.points[symbol]


def test_time_weighted_return_neutralizes_contributions_and_withdrawals():
    start = date(2026, 1, 1)
    values = [
        (start, 100.0),
        (start + timedelta(days=1), 150.0),
        (start + timedelta(days=2), 100.0),
        (start + timedelta(days=3), 110.0),
    ]
    flows = {
        start + timedelta(days=1): 50.0,
        start + timedelta(days=2): -50.0,
    }
    returns = time_weighted_returns(values, flows)
    assert [value for _, value in returns] == pytest.approx([0.0, 0.0, 0.0, 10.0])


def test_time_weighted_return_counts_fees_as_negative_performance():
    start = date(2026, 1, 1)
    returns = time_weighted_returns(
        [(start, 100.0), (start + timedelta(days=1), 200.0)],
        {start + timedelta(days=1): 101.0},
    )
    assert returns[-1][1] == pytest.approx((200 / 201 - 1) * 100)


def test_time_weighted_return_ignores_when_money_was_added():
    """10% on a small stake, then 10% on a large one: 21% either way."""
    start = date(2026, 1, 1)
    returns = time_weighted_returns(
        [
            (start, 100.0),
            (start + timedelta(days=1), 110.0),
            (start + timedelta(days=2), 10_110.0),
            (start + timedelta(days=3), 11_121.0),
        ],
        {start + timedelta(days=2): 10_000.0},
    )
    assert [value for _, value in returns] == pytest.approx([0.0, 10.0, 10.0, 21.0])


def test_time_weighted_return_starts_from_zero_and_survives_full_liquidation():
    start = date(2026, 1, 1)
    values = [
        (start, 0.0),
        (start + timedelta(days=1), 100.0),
        (start + timedelta(days=2), 110.0),
        (start + timedelta(days=3), 0.0),
        (start + timedelta(days=4), 200.0),
        (start + timedelta(days=5), 220.0),
    ]
    flows = {
        start + timedelta(days=1): 100.0,
        start + timedelta(days=3): -110.0,
        start + timedelta(days=4): 200.0,
    }
    assert time_weighted_returns(values, flows)[-1][1] == pytest.approx(21.0)


def test_benchmark_is_the_index_change_since_the_start():
    start = date(2026, 1, 1)
    middle = start + timedelta(days=5)
    end = start + timedelta(days=10)
    returns = _benchmark_index_returns(
        [(start - timedelta(days=2), 90.0), (start, 100.0), (middle, 200.0), (end, 220.0)],
        start,
        end,
        {start + timedelta(days=3)},
    )
    assert [value for _, value in returns] == pytest.approx([0.0, 0.0, 100.0, 120.0])


def test_alignment_fills_forward_across_weekends():
    friday = date(2026, 1, 2)
    saturday = friday + timedelta(days=1)
    sunday = friday + timedelta(days=2)
    monday = friday + timedelta(days=3)
    points = _fill_aligned_series(
        [(friday, 0.0), (monday, 10.0)],
        [(friday, 0.0), (saturday, 1.0), (sunday, 2.0), (monday, 3.0)],
        friday,
        monday,
    )
    assert [(point.date, point.portfolio, point.benchmark) for point in points] == [
        (friday, 0.0, 0.0),
        (saturday, 0.0, 1.0),
        (sunday, 0.0, 2.0),
        (monday, 10.0, 3.0),
    ]


@pytest.mark.asyncio
async def test_transaction_cash_flows_include_fees_and_historical_fx(
    session: AsyncSession, test_user: User, test_workspace, monkeypatch
):
    start = date(2026, 1, 1)
    transaction_date = start + timedelta(days=1)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Foreign holding",
        type="investment",
        currency="EUR",
        valuation_method="market_price",
    )
    session.add(asset)
    await session.flush()
    session.add(
        AssetTransaction(
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            kind="buy",
            quantity=Decimal("10"),
            price=Decimal("10"),
            fee=Decimal("1"),
            date=transaction_date,
        )
    )
    await session.commit()

    async def fake_convert(session, amount, from_currency, to_currency, target_date, **kwargs):
        assert (from_currency, to_currency, target_date) == (
            "EUR",
            "USD",
            transaction_date,
        )
        return amount * Decimal("2"), Decimal("2")

    monkeypatch.setattr("app.services.portfolio_performance_service.convert", fake_convert)
    contributions, withdrawals, flow_dates = await _cash_flows(
        session,
        [asset],
        [],
        "USD",
        start,
        start + timedelta(days=2),
    )
    assert set(contributions) == {transaction_date}
    assert contributions[transaction_date] == pytest.approx(202.0)
    assert withdrawals == {}
    assert flow_dates == {transaction_date}


@pytest.mark.asyncio
async def test_performance_compares_manual_asset_with_benchmark(
    session: AsyncSession, test_user: User, test_workspace
):
    end = date.today()
    start = end - timedelta(days=30)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Manual fund",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                amount=Decimal("100"),
                date=start,
                source="manual",
            ),
            AssetValue(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                amount=Decimal("110"),
                date=end,
                source="manual",
            ),
        ]
    )
    await session.commit()

    provider = FakeHistoryProvider(
        [BenchmarkHistoryPoint(start, 200.0), BenchmarkHistoryPoint(end, 210.0)]
    )
    result = await get_portfolio_performance(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        "yahoo",
        "^TEST",
        "3m",
        end_date=end,
        provider=provider,  # type: ignore[arg-type]
    )
    assert result.portfolio_return == pytest.approx(10.0)
    assert result.benchmark_return == pytest.approx(5.0)
    assert result.excess_return == pytest.approx(5.0)
    assert result.points[0].portfolio == result.points[0].benchmark == 0.0


@pytest.mark.asyncio
async def test_later_manual_asset_is_a_contribution_not_performance(
    session: AsyncSession, test_user: User, test_workspace
):
    end = date.today()
    start = end - timedelta(days=30)
    later = start + timedelta(days=10)
    first = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="First",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    second = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Second",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=later,
        purchase_price=Decimal("50"),
    )
    session.add_all([first, second])
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=first.id,
                workspace_id=test_workspace.id,
                amount=Decimal("100"),
                date=start,
                source="manual",
            ),
            AssetValue(
                asset_id=first.id,
                workspace_id=test_workspace.id,
                amount=Decimal("100"),
                date=end,
                source="manual",
            ),
            AssetValue(
                asset_id=second.id,
                workspace_id=test_workspace.id,
                amount=Decimal("50"),
                date=later,
                source="manual",
            ),
            AssetValue(
                asset_id=second.id,
                workspace_id=test_workspace.id,
                amount=Decimal("50"),
                date=end,
                source="manual",
            ),
        ]
    )
    await session.commit()
    provider = FakeHistoryProvider(
        [BenchmarkHistoryPoint(start, 100.0), BenchmarkHistoryPoint(end, 100.0)]
    )
    result = await get_portfolio_performance(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        "yahoo",
        "^FLAT",
        "3m",
        end_date=end,
        provider=provider,  # type: ignore[arg-type]
    )
    assert result.portfolio_return == pytest.approx(0.0)
    assert result.benchmark_return == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_sparse_synced_history_jumps_to_time_weighted_return(
    session: AsyncSession, test_user: User, test_workspace
):
    sync_date = date.today()
    start = sync_date - timedelta(days=30)
    later = sync_date - timedelta(days=15)
    metadata = {"type": "FIXED_INCOME", "status": "ACTIVE"}
    first = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="First synced deposit",
        type="investment",
        currency="BRL",
        valuation_method="manual",
        source="pluggy",
        external_metadata=metadata,
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    second = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Later synced deposit",
        type="investment",
        currency="BRL",
        valuation_method="manual",
        source="pluggy",
        external_metadata=metadata,
        purchase_date=later,
        purchase_price=Decimal("100"),
    )
    session.add_all([first, second])
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=first.id,
                workspace_id=test_workspace.id,
                amount=Decimal("100"),
                gross_amount=Decimal("100"),
                date=start,
                source="sync",
            ),
            AssetValue(
                asset_id=second.id,
                workspace_id=test_workspace.id,
                amount=Decimal("100"),
                gross_amount=Decimal("100"),
                date=later,
                source="sync",
            ),
            AssetValue(
                asset_id=first.id,
                workspace_id=test_workspace.id,
                amount=Decimal("107.75"),
                gross_amount=Decimal("110"),
                date=sync_date,
                source="sync",
            ),
            AssetValue(
                asset_id=second.id,
                workspace_id=test_workspace.id,
                amount=Decimal("103.875"),
                gross_amount=Decimal("105"),
                date=sync_date,
                source="sync",
            ),
        ]
    )
    await session.commit()

    provider = FakeHistoryProvider(
        [
            BenchmarkHistoryPoint(start, 100.0),
            BenchmarkHistoryPoint(later, 105.0),
            BenchmarkHistoryPoint(sync_date, 110.0),
        ]
    )
    result = await get_portfolio_performance(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        "yahoo",
        "^TEST",
        "3m",
        end_date=sync_date,
        provider=provider,  # type: ignore[arg-type]
    )
    assert result.portfolio_return == pytest.approx(7.5)
    # The index itself: 100 -> 110.
    assert result.benchmark_return == pytest.approx(10.0)
    assert next(point for point in result.points if point.date == later).portfolio == 0.0
    assert result.points[-1].portfolio == pytest.approx(7.5)


@pytest.mark.asyncio
async def test_synced_fixed_income_rebalance_is_not_performance(
    session: AsyncSession, test_user: User, test_workspace
):
    end = date.today()
    start = end - timedelta(days=30)
    rebalance = start + timedelta(days=10)
    metadata = {"type": "FIXED_INCOME", "status": "ACTIVE"}
    original = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Original fixed income",
        type="investment",
        currency="BRL",
        valuation_method="manual",
        source="pluggy",
        external_metadata=metadata,
        purchase_date=start,
        purchase_price=Decimal("7116.87"),
    )
    replacements = [
        Asset(
            id=uuid.uuid4(),
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            name=f"Replacement {index}",
            type="investment",
            currency="BRL",
            valuation_method="manual",
            source="pluggy",
            external_metadata=metadata,
            purchase_date=rebalance,
            purchase_price=amount,
        )
        for index, amount in enumerate((Decimal("3009.32"), Decimal("540.92")), start=1)
    ]
    session.add_all([original, *replacements])
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=original.id,
                workspace_id=test_workspace.id,
                amount=Decimal("7116.87"),
                date=start,
                source="pluggy",
            ),
            AssetValue(
                asset_id=original.id,
                workspace_id=test_workspace.id,
                amount=Decimal("2116.96"),
                date=rebalance,
                source="pluggy",
            ),
            AssetValue(
                asset_id=replacements[0].id,
                workspace_id=test_workspace.id,
                amount=Decimal("3009.32"),
                date=rebalance,
                source="pluggy",
            ),
            AssetValue(
                asset_id=replacements[1].id,
                workspace_id=test_workspace.id,
                amount=Decimal("540.92"),
                date=rebalance,
                source="pluggy",
            ),
        ]
    )
    await session.commit()

    provider = FakeHistoryProvider(
        [BenchmarkHistoryPoint(start, 100.0), BenchmarkHistoryPoint(end, 100.0)]
    )
    result = await get_portfolio_performance(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        "yahoo",
        "^FLAT",
        "3m",
        end_date=end,
        provider=provider,  # type: ignore[arg-type]
    )
    assert result.portfolio_return == pytest.approx(0.0)
    assert result.benchmark_return == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_synced_equity_decline_remains_negative_performance(
    session: AsyncSession, test_user: User, test_workspace
):
    end = date.today()
    start = end - timedelta(days=30)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Synced equity",
        type="investment",
        currency="BRL",
        valuation_method="manual",
        source="pluggy",
        external_metadata={"type": "EQUITY", "status": "ACTIVE"},
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                amount=Decimal("100"),
                date=start,
                source="pluggy",
            ),
            AssetValue(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                amount=Decimal("90"),
                date=end,
                source="pluggy",
            ),
        ]
    )
    await session.commit()

    provider = FakeHistoryProvider(
        [BenchmarkHistoryPoint(start, 100.0), BenchmarkHistoryPoint(end, 100.0)]
    )
    result = await get_portfolio_performance(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        "yahoo",
        "^FLAT",
        "3m",
        end_date=end,
        provider=provider,  # type: ignore[arg-type]
    )
    assert result.portfolio_return == pytest.approx(-10.0)


def test_snapshot_flows_price_share_count_changes_as_contributions():
    start = date(2026, 9, 28)
    flows = snapshot_position_flows(
        [
            (start, 589.69, 30.0),
            # 40 more shares bought while the price moved 19.66 -> 19.02.
            (start + timedelta(days=1), 1331.40, 70.0),
            (start + timedelta(days=2), 1345.16, 70.0),
        ]
    )
    assert flows == [(start + timedelta(days=1), pytest.approx(40 * 1331.40 / 70))]


def test_snapshot_flows_ignore_splits():
    start = date(2026, 9, 1)
    flows = snapshot_position_flows(
        [(start, 1000.0, 10.0), (start + timedelta(days=1), 1010.0, 20.0)]
    )
    assert flows == []


def test_snapshot_flows_never_infer_a_purchase_from_value_alone():
    start = date(2026, 9, 28)
    flows = snapshot_position_flows(
        [(start, 25303.44, None), (start + timedelta(days=1), 34240.29, None)]
    )
    assert flows == []


def test_ledger_flows_land_on_the_first_snapshot_that_reflects_them():
    """AUPO11: the 09-28 buy is not in that day's snapshot, only in 09-29's."""
    flows = snapshot_ledger_flows(
        [
            (date(2026, 9, 2), Decimal("228"), Decimal("25009.32")),  # before any snapshot
            (date(2026, 9, 28), Decimal("81"), Decimal("8973.18")),
            (date(2026, 9, 30), Decimal("1"), Decimal("111")),  # no snapshot reflects it yet
        ],
        [
            (date(2026, 9, 25), 25303.44, None),
            (date(2026, 9, 28), 25303.44, None),
            (date(2026, 9, 29), 34240.29, None),
            (date(2026, 9, 30), 34240.29, None),
        ],
    )
    assert flows == [
        (date(2026, 9, 2), Decimal("25009.32")),
        (date(2026, 9, 29), Decimal("8973.18")),
    ]


def test_ledger_flows_use_a_same_day_snapshot_that_already_reflects_the_trade():
    """NU CDB: the 09-02 snapshot already shows that day's redemption."""
    flows = snapshot_ledger_flows(
        [
            (date(2026, 7, 1), Decimal("1000000"), Decimal("10000")),
            (date(2026, 9, 2), Decimal("-761481.61895785"), Decimal("-7795.63")),
        ],
        [
            (date(2026, 8, 3), 10121.52, None),
            (date(2026, 9, 1), 10232.16, None),
            (date(2026, 9, 2), 2441.82, None),
            (date(2026, 9, 3), 2443.08, None),
        ],
    )
    assert flows == [
        (date(2026, 7, 1), Decimal("10000")),
        (date(2026, 9, 2), Decimal("-7795.63")),
    ]


def test_ledger_flows_prefer_the_stored_share_count():
    flows = snapshot_ledger_flows(
        [
            (date(2026, 9, 1), Decimal("10"), Decimal("1000")),
            (date(2026, 9, 28), Decimal("5"), Decimal("500")),
        ],
        [
            (date(2026, 9, 1), 1000.0, 10.0),
            (date(2026, 9, 28), 1500.0, 10.0),
            (date(2026, 9, 29), 1500.0, 15.0),
        ],
    )
    assert flows[-1] == (date(2026, 9, 29), Decimal("500"))


def test_ledger_flows_survive_a_day_whose_trades_net_to_no_cash():
    """Buying 10 at 10 and selling 5 at 20 on one day moves shares but no cash."""
    day = date(2026, 9, 10)
    flows = snapshot_ledger_flows(
        [(day, Decimal("10"), Decimal("100")), (day, Decimal("-5"), Decimal("-100"))],
        [
            (date(2026, 9, 1), 500.0, None),
            (day, 600.0, None),
            (date(2026, 9, 11), 600.0, None),
        ],
    )
    assert flows == [(date(2026, 9, 11), Decimal("0"))]


@pytest.mark.asyncio
async def test_performance_ends_on_the_workspace_today(
    session: AsyncSession, test_user: User, test_workspace, monkeypatch
):
    """Sync dates snapshots in the workspace's time zone, which can run a day
    ahead of the server's clock."""
    workspace_today = date.today() + timedelta(days=1)
    monkeypatch.setattr(
        "app.services.portfolio_performance_service.app_today", lambda: workspace_today
    )
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Savings",
        type="investment",
        currency="BRL",
        valuation_method="manual",
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                amount=Decimal(amount),
                date=when,
                source="manual",
            )
            for when, amount in (
                (workspace_today - timedelta(days=10), "100"),
                (workspace_today, "110"),
            )
        ]
    )
    await session.commit()

    result = await get_portfolio_performance_multi(
        session, test_workspace.id, test_user.id, "BRL", [], "3m"
    )

    assert result.end_date == workspace_today
    assert result.portfolio_return == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_synced_position_top_up_is_a_contribution_not_performance(
    session: AsyncSession, test_user: User, test_workspace
):
    end = date.today()
    start = end - timedelta(days=10)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Synced ETF",
        type="investment",
        currency="USD",
        valuation_method="manual",
        source="broker",
    )
    session.add(asset)
    await session.flush()
    # 10 shares at 100 -> 102, then 10 more shares bought, then 105.
    snapshots = [
        (start, Decimal("1000"), Decimal("100")),
        (start + timedelta(days=5), Decimal("1020"), Decimal("102")),
        (start + timedelta(days=6), Decimal("2040"), Decimal("102")),
        (end, Decimal("2100"), Decimal("105")),
    ]
    session.add_all(
        AssetValue(
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            amount=amount,
            price=price,
            date=point_date,
            source="sync",
        )
        for point_date, amount, price in snapshots
    )
    await session.commit()

    provider = FakeHistoryProvider(
        [BenchmarkHistoryPoint(start, 100.0), BenchmarkHistoryPoint(end, 100.0)]
    )
    result = await get_portfolio_performance(
        session,
        test_workspace.id,
        test_user.id,
        "USD",
        "yahoo",
        "^FLAT",
        "3m",
        end_date=end,
        provider=provider,  # type: ignore[arg-type]
    )
    # The price went 100 -> 105: +5% however many shares were added on the
    # way, not the +110% a raw valuation comparison would show.
    assert result.portfolio_return == pytest.approx(5.0, abs=1e-5)


@pytest.mark.asyncio
async def test_synced_ledger_counts_purchases_exactly(
    session: AsyncSession, test_user: User, test_workspace
):
    """AUPO11: bought 228 before Securo's first snapshot and 81 more on 09-28,
    which the provider snapshot first shows on 09-29."""
    end = date(2026, 10, 2)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="AUPO11",
        type="investment",
        currency="BRL",
        valuation_method="manual",
        source="pluggy",
        external_metadata={"type": "EQUITY"},
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        AssetValue(
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            amount=Decimal(amount),
            date=point_date,
            source="sync",
        )
        for point_date, amount in [
            (date(2026, 9, 25), "25303.44"),
            (date(2026, 9, 28), "25303.44"),
            (date(2026, 9, 29), "34240.29"),
            (date(2026, 10, 2), "34336.08"),
        ]
    )
    session.add_all(
        AssetTransaction(
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            kind="buy",
            quantity=Decimal(quantity),
            price=Decimal(price),
            fee=Decimal(fee),
            date=trade_date,
            source="pluggy",
        )
        for trade_date, quantity, price, fee in [
            (date(2026, 9, 2), "228", "109.69", "4.50"),
            (date(2026, 9, 28), "81", "110.78", "0"),
        ]
    )
    await session.commit()

    result = await get_portfolio_performance_multi(
        session, test_workspace.id, test_user.id, "BRL", [], "3m", end_date=end
    )
    # The period opens at zero the day before the first buy; each interval
    # compounds net of the money added, the first buy's commission included.
    assert result.start_date == date(2026, 9, 1)
    growth = (
        25009.32
        / (25009.32 + 4.50)
        * 25303.44
        / 25009.32
        * 34240.29
        / (25303.44 + 8973.18)
        * 34336.08
        / 34240.29
    )
    assert result.portfolio_return == pytest.approx((growth - 1) * 100, abs=1e-5)
    by_date = {point.date: point.portfolio for point in result.points}
    # No dip on the trade date: the cash is booked when the value shows it.
    assert by_date[date(2026, 9, 28)] == pytest.approx(by_date[date(2026, 9, 25)])


@pytest.mark.asyncio
async def test_placeholder_after_partial_redemption_is_not_a_valuation(
    session: AsyncSession, test_user: User, test_workspace
):
    """NU CDB: R$10,000 bought 07-01. After a partial redemption Pluggy's
    purchase price is the remaining cost (2,385.18), so the 07-01 placeholder
    no longer equals it, yet it must still not count as a snapshot."""
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="CDB",
        type="investment",
        currency="BRL",
        valuation_method="manual",
        source="pluggy",
        external_metadata={"type": "FIXED_INCOME"},
        purchase_date=date(2026, 7, 1),
        purchase_price=Decimal("2385.18"),
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        AssetValue(
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            amount=Decimal(amount),
            date=point_date,
            source="sync",
        )
        for point_date, amount in [
            (date(2026, 7, 1), "10000"),
            (date(2026, 8, 3), "10121.52"),
            (date(2026, 9, 1), "10232.16"),
        ]
    )
    session.add(
        AssetTransaction(
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            kind="buy",
            quantity=Decimal("1000000"),
            price=Decimal("0.01"),
            date=date(2026, 7, 1),
            source="pluggy",
        )
    )
    await session.commit()

    result = await get_portfolio_performance_multi(
        session, test_workspace.id, test_user.id, "BRL", [], "6m", end_date=date(2026, 9, 1)
    )
    by_date = {point.date: point.portfolio for point in result.points}
    assert by_date[date(2026, 7, 1)] == pytest.approx(0.0)
    assert result.portfolio_return == pytest.approx(232.16 / 10000 * 100, abs=1e-5)


@pytest.mark.asyncio
async def test_active_collection_filters_portfolio_assets(
    session: AsyncSession, test_user: User, test_workspace
):
    end = date.today()
    start = end - timedelta(days=30)
    included_group = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Included",
    )
    excluded_group = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Excluded",
    )
    session.add_all([included_group, excluded_group])
    await session.flush()

    included = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        group_id=included_group.id,
        name="Included asset",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    excluded = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        group_id=excluded_group.id,
        name="Excluded asset",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    session.add_all([included, excluded])
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=included.id,
                workspace_id=test_workspace.id,
                amount=Decimal("100"),
                date=start,
                source="manual",
            ),
            AssetValue(
                asset_id=included.id,
                workspace_id=test_workspace.id,
                amount=Decimal("110"),
                date=end,
                source="manual",
            ),
            AssetValue(
                asset_id=excluded.id,
                workspace_id=test_workspace.id,
                amount=Decimal("100"),
                date=start,
                source="manual",
            ),
            AssetValue(
                asset_id=excluded.id,
                workspace_id=test_workspace.id,
                amount=Decimal("200"),
                date=end,
                source="manual",
            ),
        ]
    )
    await session.commit()
    provider = FakeHistoryProvider(
        [BenchmarkHistoryPoint(start, 100.0), BenchmarkHistoryPoint(end, 100.0)]
    )
    result = await get_portfolio_performance(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        "yahoo",
        "^FLAT",
        "3m",
        asset_group_ids=[included_group.id],
        end_date=end,
        provider=provider,
    )
    assert result.portfolio_return == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_multiple_benchmarks_share_one_portfolio_series(
    session: AsyncSession, test_user: User, test_workspace
):
    end = date.today()
    start = end - timedelta(days=30)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Shared portfolio",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        [
            AssetValue(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                amount=Decimal("100"),
                date=start,
                source="manual",
            ),
            AssetValue(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                amount=Decimal("110"),
                date=end,
                source="manual",
            ),
        ]
    )
    await session.commit()

    provider = MultiHistoryProvider(
        {
            "^FIVE": [
                BenchmarkHistoryPoint(start, 100.0),
                BenchmarkHistoryPoint(end, 105.0),
            ],
            "^TWENTY": [
                BenchmarkHistoryPoint(start, 100.0),
                BenchmarkHistoryPoint(end, 120.0),
            ],
        }
    )
    result = await get_portfolio_performance_multi(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        [("yahoo", "^FIVE"), ("yahoo", "^TWENTY")],
        "3m",
        end_date=end,
        provider=provider,
    )

    assert set(provider.calls) == {"^FIVE", "^TWENTY"}
    assert result.portfolio_return == pytest.approx(10.0)
    assert [metric.benchmark_return for metric in result.benchmarks] == pytest.approx([5.0, 20.0])
    assert [metric.excess_return for metric in result.benchmarks] == pytest.approx([5.0, -10.0])
    assert result.points[-1].benchmarks == pytest.approx(
        {"yahoo:^FIVE": 5.0, "yahoo:^TWENTY": 20.0}
    )


@pytest.mark.asyncio
async def test_custom_scope_unites_whole_wallets_and_individual_assets(
    session: AsyncSession, test_user: User, test_workspace
):
    end = date.today()
    start = end - timedelta(days=30)
    selected_group = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Selected wallet",
    )
    other_group = AssetGroup(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Other wallet",
    )
    session.add_all([selected_group, other_group])
    await session.flush()
    wallet_asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        group_id=selected_group.id,
        name="Wallet asset",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    individual_asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        group_id=other_group.id,
        name="Selected individual",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    excluded_asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        group_id=other_group.id,
        name="Excluded individual",
        type="fund",
        currency="BRL",
        valuation_method="manual",
        purchase_date=start,
        purchase_price=Decimal("100"),
    )
    session.add_all([wallet_asset, individual_asset, excluded_asset])
    await session.flush()
    for asset, final_value in (
        (wallet_asset, "110"),
        (individual_asset, "130"),
        (excluded_asset, "200"),
    ):
        session.add_all(
            [
                AssetValue(
                    asset_id=asset.id,
                    workspace_id=test_workspace.id,
                    amount=Decimal("100"),
                    date=start,
                    source="manual",
                ),
                AssetValue(
                    asset_id=asset.id,
                    workspace_id=test_workspace.id,
                    amount=Decimal(final_value),
                    date=end,
                    source="manual",
                ),
            ]
        )
    await session.commit()
    provider = FakeHistoryProvider(
        [BenchmarkHistoryPoint(start, 100.0), BenchmarkHistoryPoint(end, 100.0)]
    )

    result = await get_portfolio_performance(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        "yahoo",
        "^FLAT",
        "3m",
        selected_asset_group_ids=[selected_group.id],
        asset_ids=[individual_asset.id],
        end_date=end,
        provider=provider,
    )

    assert result.portfolio_return == pytest.approx(20.0)
