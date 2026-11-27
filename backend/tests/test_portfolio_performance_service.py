from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.models.fx_rate import FxRate
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
        fees={start + timedelta(days=1): 1.0},
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


def test_time_weighted_return_keeps_a_buy_at_the_days_price_out_of_that_days_move():
    """+10% on 100, a 1000 buy at the new price, then +10%: 21%, not 11%."""
    start = date(2026, 1, 1)
    shares = 100 + 1000 / 1.1
    returns = time_weighted_returns(
        [
            (start, 100.0),
            (start + timedelta(days=1), shares * 1.1),
            (start + timedelta(days=2), shares * 1.21),
        ],
        {start + timedelta(days=1): 1000.0},
    )
    assert [value for _, value in returns] == pytest.approx([0.0, 10.0, 21.0])


def test_time_weighted_return_exposes_money_bought_at_the_previous_price():
    """A purchase only the next snapshot shows was made before that move."""
    start = date(2026, 1, 1)
    returns = time_weighted_returns(
        [(start, 100.0), (start + timedelta(days=1), 1210.0)],
        {},
        opening={start + timedelta(days=1): 1000.0},
    )
    assert returns[-1][1] == pytest.approx(10.0)


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
    flows = await _cash_flows(
        session,
        [asset],
        [],
        "USD",
        start,
        start + timedelta(days=2),
    )
    # A market-priced buy is recorded at its own price, split from its fee.
    [trade] = flows.trades[transaction_date]
    assert trade.asset_key == str(asset.id)
    assert trade.mark == pytest.approx(0.0)
    assert trade.amount == pytest.approx(200.0)
    assert trade.fee == pytest.approx(2.0)
    assert flows.contributions == {}
    assert flows.fees == {}
    assert flows.withdrawals == {}
    assert flows.opening == {}
    assert flows.dates == {transaction_date}


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
async def test_a_split_is_not_a_loss_when_the_price_drops_with_it(
    session: AsyncSession, test_user: User, test_workspace
):
    """10 shares at 100 become 100 shares at 10: the holding is worth the same."""
    end = date.today()
    start = end - timedelta(days=30)
    split_on = start + timedelta(days=10)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Split stock",
        type="stock",
        currency="BRL",
        valuation_method="market_price",
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        [
            AssetTransaction(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                kind="buy",
                quantity=Decimal("10"),
                price=Decimal("100"),
                date=start,
            ),
            AssetTransaction(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                kind="split",
                quantity=Decimal("0"),
                price=Decimal("0"),
                old_units=1,
                new_units=10,
                date=split_on,
            ),
        ]
    )
    # Stored prices are never adjusted for a split: 100 before it, 10 after.
    for when, price in (
        (start, "100"),
        (split_on - timedelta(days=1), "100"),
        (split_on + timedelta(days=1), "10"),
        (end, "10"),
    ):
        session.add(
            AssetValue(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                amount=Decimal("1000"),
                price=Decimal(price),
                date=when,
                source="market",
            )
        )
    await session.commit()

    provider = FakeHistoryProvider(
        [BenchmarkHistoryPoint(start, 200.0), BenchmarkHistoryPoint(end, 200.0)]
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
    assert result.portfolio_return == pytest.approx(0.0, abs=1e-6)
    assert all(point.portfolio == pytest.approx(0.0, abs=1e-6) for point in result.points)


async def _flat_brl_fund(session: AsyncSession, user: User, workspace, start: date, end: date):
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Manual fund",
        type="fund",
        currency="BRL",
        valuation_method="manual",
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        AssetValue(
            asset_id=asset.id,
            workspace_id=workspace.id,
            amount=Decimal("100"),
            date=when,
            source="manual",
        )
        for when in (start, end)
    )


@pytest.mark.asyncio
async def test_benchmark_in_another_currency_is_converted_at_each_days_rate(
    session: AsyncSession, test_user: User, test_workspace
):
    """A USD index up 10% while the dollar rose 10% against the real: +21% in BRL."""
    end = date.today()
    start = end - timedelta(days=30)
    await _flat_brl_fund(session, test_user, test_workspace, start, end)
    session.add_all(
        FxRate(base_currency="USD", quote_currency="BRL", date=when, rate=rate, source="test")
        for when, rate in ((start - timedelta(days=3), Decimal("5")), (end, Decimal("5.5")))
    )
    await session.commit()

    provider = FakeHistoryProvider(
        [BenchmarkHistoryPoint(start, 200.0, "USD"), BenchmarkHistoryPoint(end, 220.0, "USD")]
    )
    result = await get_portfolio_performance(
        session, test_workspace.id, test_user.id, "BRL", "yahoo", "^GSPC", "3m",
        end_date=end, provider=provider,  # type: ignore[arg-type]
    )
    assert result.benchmark_return == pytest.approx(21.0)


@pytest.mark.asyncio
async def test_benchmark_converts_at_the_comparison_dates_rate_after_its_last_close(
    session: AsyncSession, test_user: User, test_workspace
):
    """A flat USD index whose last close is Friday, compared on Monday after the
    dollar rose 10%: the benchmark gains the 10% a USD holding would show."""
    end = date.today()
    start = end - timedelta(days=30)
    await _flat_brl_fund(session, test_user, test_workspace, start, end)
    session.add_all(
        FxRate(base_currency="USD", quote_currency="BRL", date=when, rate=rate, source="test")
        for when, rate in ((start - timedelta(days=3), Decimal("5")), (end, Decimal("5.5")))
    )
    await session.commit()

    provider = FakeHistoryProvider(
        [
            BenchmarkHistoryPoint(start, 200.0, "USD"),
            BenchmarkHistoryPoint(end - timedelta(days=3), 200.0, "USD"),
        ]
    )
    result = await get_portfolio_performance(
        session, test_workspace.id, test_user.id, "BRL", "yahoo", "^GSPC", "3m",
        end_date=end, provider=provider,  # type: ignore[arg-type]
    )
    assert result.benchmark_return == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_benchmark_converts_on_dates_only_another_benchmark_adds(
    session: AsyncSession, test_user: User, test_workspace
):
    """A BRL portfolio valued on Monday and Friday, a flat USD index closing on
    those days and a BRL index closing daily. The dollar rises 10% on
    Wednesday: the USD index reads +10% from Wednesday on, not 0% until a
    later portfolio date picks up the move."""
    monday, wednesday, friday = date(2026, 6, 22), date(2026, 6, 24), date(2026, 6, 26)
    start = friday - timedelta(days=90)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Manual fund",
        type="fund",
        currency="BRL",
        valuation_method="manual",
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        AssetValue(
            asset_id=asset.id,
            workspace_id=test_workspace.id,
            amount=Decimal("100"),
            date=when,
            source="manual",
        )
        for when in (start, monday, friday)
    )
    session.add_all(
        FxRate(
            base_currency="USD",
            quote_currency="BRL",
            date=start + timedelta(days=offset),
            rate=Decimal("5.5") if start + timedelta(days=offset) >= wednesday else Decimal("5"),
            source="test",
        )
        for offset in range(-10, 91)
    )
    await session.commit()

    provider = MultiHistoryProvider(
        {
            "^USD": [
                BenchmarkHistoryPoint(when, 100.0, "USD") for when in (start, monday, friday)
            ],
            "^BRL": [
                BenchmarkHistoryPoint(start + timedelta(days=offset), 100.0, "BRL")
                for offset in range(91)
            ],
        }
    )
    result = await get_portfolio_performance_multi(
        session, test_workspace.id, test_user.id, "BRL",
        [("yahoo", "^USD"), ("yahoo", "^BRL")], "3m",
        end_date=friday, provider=provider,  # type: ignore[arg-type]
    )
    by_date = {point.date: point.benchmarks for point in result.points}
    assert by_date[monday]["yahoo:^USD"] == pytest.approx(0.0)
    assert by_date[wednesday]["yahoo:^USD"] == pytest.approx(10.0)
    assert by_date[friday]["yahoo:^USD"] == pytest.approx(10.0)
    assert result.benchmarks[0].benchmark_return == pytest.approx(10.0)
    assert result.portfolio_return == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_benchmark_without_a_stored_rate_reports_it_instead_of_mixing_currencies(
    session: AsyncSession, test_user: User, test_workspace
):
    end = date.today()
    start = end - timedelta(days=30)
    await _flat_brl_fund(session, test_user, test_workspace, start, end)
    await session.commit()

    provider = FakeHistoryProvider(
        [BenchmarkHistoryPoint(start, 200.0, "USD"), BenchmarkHistoryPoint(end, 220.0, "USD")]
    )
    result = await get_portfolio_performance(
        session, test_workspace.id, test_user.id, "BRL", "yahoo", "^GSPC", "3m",
        end_date=end, provider=provider,  # type: ignore[arg-type]
    )
    assert result.benchmark_return is None
    assert result.benchmarks[0].source_error == "no_fx_rate"
    assert result.portfolio_return == pytest.approx(0.0)


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
        (date(2026, 9, 2), Decimal("25009.32"), date(2026, 9, 2)),
        (date(2026, 9, 29), Decimal("8973.18"), date(2026, 9, 28)),
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
        (date(2026, 7, 1), Decimal("10000"), date(2026, 7, 1)),
        (date(2026, 9, 2), Decimal("-7795.63"), date(2026, 9, 2)),
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
    assert flows[-1] == (date(2026, 9, 29), Decimal("500"), date(2026, 9, 28))


def test_ledger_flows_wait_past_a_snapshot_that_still_shows_the_old_count():
    """A provider can take more than a day to show a buy (settlement)."""
    flows = snapshot_ledger_flows(
        [
            (date(2026, 9, 1), Decimal("10"), Decimal("1000")),
            (date(2026, 9, 28), Decimal("5"), Decimal("500")),
        ],
        [
            (date(2026, 9, 1), 1000.0, 10.0),
            (date(2026, 9, 28), 1000.0, 10.0),
            (date(2026, 9, 29), 1000.0, 10.0),
            (date(2026, 9, 30), 1500.0, 15.0),
        ],
    )
    assert flows[-1] == (date(2026, 9, 30), Decimal("500"), date(2026, 9, 28))


def test_ledger_flows_count_a_same_day_round_trip_on_its_own_day():
    """Selling 10 at 110 and buying 10 back at 100 leaves the count at 10, so
    no snapshot can show it; the 100 taken out belongs to that day."""
    day = date(2026, 9, 10)
    flows = snapshot_ledger_flows(
        [
            (date(2026, 9, 1), Decimal("10"), Decimal("1000")),
            (day, Decimal("-10"), Decimal("-1100")),
            (day, Decimal("10"), Decimal("1000")),
        ],
        [
            (date(2026, 9, 1), 1000.0, 10.0),
            (day, 1000.0, 10.0),
            (date(2026, 9, 11), 1000.0, 10.0),
        ],
    )
    assert flows[-1] == (day, Decimal("-100"), day)


def test_ledger_flows_match_each_trade_when_two_are_in_flight():
    """Buys on consecutive days, each shown the next day: a snapshot that shows
    the first buy but not yet the second takes only the first."""
    flows = snapshot_ledger_flows(
        [
            (date(2026, 9, 1), Decimal("100"), Decimal("1000")),
            (date(2026, 9, 10), Decimal("10"), Decimal("100")),
            (date(2026, 9, 11), Decimal("10"), Decimal("100")),
        ],
        [
            (date(2026, 9, 1), 1000.0, 100.0),
            (date(2026, 9, 10), 1000.0, 100.0),
            (date(2026, 9, 11), 1100.0, 110.0),
            (date(2026, 9, 12), 1200.0, 120.0),
        ],
    )
    assert flows[1:] == [
        (date(2026, 9, 11), Decimal("100"), date(2026, 9, 10)),
        (date(2026, 9, 12), Decimal("100"), date(2026, 9, 11)),
    ]


def test_ledger_flows_count_shares_at_the_trade_price_only_on_the_trade_date():
    """Without a stored count, a later snapshot's value at the trade's price
    says more about the price since than about the shares: the trade lands
    on the next snapshot instead of waiting for the price to come back."""
    flows = snapshot_ledger_flows(
        [
            (date(2026, 9, 1), Decimal("100"), Decimal("1000")),
            (date(2026, 9, 10), Decimal("3"), Decimal("30")),
        ],
        [
            (date(2026, 9, 1), 1000.0, None),
            (date(2026, 9, 10), 1009.4, None),  # 103 units at 9.80
            (date(2026, 9, 11), 1009.4, None),
            (date(2026, 9, 24), 1019.7, None),  # 103 units at 9.90
        ],
    )
    assert flows[-1] == (date(2026, 9, 11), Decimal("30"), date(2026, 9, 10))


@pytest.mark.parametrize("quantity", [Decimal("105"), Decimal("-90")])
def test_ledger_flows_without_a_count_wait_for_the_value_to_show_the_trade(quantity):
    """A Friday trade a provider shows on Monday, synced daily with no share
    count: the weekend snapshots still hold the old value, so the cash waits
    for the snapshot whose value moved by it."""
    price = Decimal("110.78")
    friday = date(2026, 9, 25)
    snapshots = [
        (
            date(2026, 9, 21) + timedelta(days=offset),
            float((300 + (quantity if offset >= 7 else 0)) * price),
            None,
        )
        for offset in range(11)
    ]
    flows = snapshot_ledger_flows(
        [
            (date(2026, 9, 2), Decimal("300"), Decimal("300") * price),
            (friday, quantity, quantity * price),
        ],
        snapshots,
    )
    assert flows[-1] == (date(2026, 9, 28), quantity * price, friday)


def test_ledger_flows_wait_for_both_legs_of_a_round_trip_a_lagging_provider_shows():
    """Bought on day 10 and sold on day 12 at a provider three days behind: the
    day 12 count still equals the state after both trades, but the snapshots
    after it show the buy first, so neither lands on day 12."""
    start = date(2026, 9, 1)
    trades = [(0, Decimal("500000")), (10, Decimal("155000")), (12, Decimal("-155000"))]
    unit = Decimal("0.01007794")
    snapshots: list[tuple[date, float, Optional[float]]] = []
    for offset in range(5, 21):
        shown = sum(quantity for day, quantity in trades if day <= offset - 3)
        snapshots.append((start + timedelta(days=offset), float(shown * unit), float(shown)))
    flows = snapshot_ledger_flows(
        [(start + timedelta(days=day), quantity, quantity * unit) for day, quantity in trades],
        snapshots,
    )
    assert flows[1:] == [
        (date(2026, 9, 14), Decimal("155000") * unit, date(2026, 9, 11)),
        (date(2026, 9, 16), Decimal("-155000") * unit, date(2026, 9, 13)),
    ]


def test_ledger_flows_leave_out_a_buy_the_share_counts_do_not_show_yet():
    """Snapshots through the period's end still hold 10 units: the bought
    shares are not in the valuation, so their cash is not counted either."""
    flows = snapshot_ledger_flows(
        [
            (date(2026, 9, 1), Decimal("10"), Decimal("1000")),
            (date(2026, 9, 28), Decimal("5"), Decimal("500")),
        ],
        [
            (date(2026, 9, 1), 1000.0, 10.0),
            (date(2026, 9, 28), 1000.0, 10.0),
            (date(2026, 9, 29), 1000.0, 10.0),
        ],
    )
    assert flows == [(date(2026, 9, 1), Decimal("1000"), date(2026, 9, 1))]


def test_ledger_flows_without_a_count_do_not_take_a_trade_on_a_half_move():
    """A buy of 1000 against a value up 500 is as far off as no trade at all:
    it waits for the snapshot that moved by its cash."""
    flows = snapshot_ledger_flows(
        [
            (date(2026, 9, 1), Decimal("10"), Decimal("1000")),
            (date(2026, 9, 10), Decimal("10"), Decimal("1000")),
        ],
        [
            (date(2026, 9, 9), 1000.0, None),
            (date(2026, 9, 10), 1000.0, None),
            (date(2026, 9, 11), 1500.0, None),
            (date(2026, 9, 12), 3000.0, None),
        ],
    )
    assert flows[-1] == (date(2026, 9, 12), Decimal("1000"), date(2026, 9, 10))


def test_ledger_flows_never_drop_a_trade_a_later_one_offsets():
    """A buy the provider never shows, sold the next day: both flows count."""
    flows = snapshot_ledger_flows(
        [
            (date(2026, 9, 1), Decimal("10"), Decimal("1000")),
            (date(2026, 9, 2), Decimal("5"), Decimal("500")),
            (date(2026, 9, 3), Decimal("-5"), Decimal("-550")),
        ],
        [
            (date(2026, 9, 1), 1000.0, 10.0),
            (date(2026, 9, 2), 1000.0, 10.0),
            (date(2026, 9, 3), 1000.0, 10.0),
            (date(2026, 9, 4), 1000.0, 10.0),
        ],
    )
    assert sum(flow for _, flow, _ in flows) == Decimal("950")


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
    assert flows == [(date(2026, 9, 11), Decimal("0"), day)]


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


@pytest.mark.asyncio
async def test_selling_and_buying_back_the_same_day_keeps_the_days_move(
    session: AsyncSession, test_user: User, test_workspace
):
    """A sale of shares held that morning is not a day trade of that day's buy."""
    end = date.today()
    day0 = end - timedelta(days=20)
    day1 = day0 + timedelta(days=1)
    asset = Asset(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Round trip",
        type="stock",
        currency="BRL",
        valuation_method="market_price",
        ticker="RTRP3",
    )
    session.add(asset)
    await session.flush()
    for kind, when, price in (("buy", day0, "100"), ("sell", day1, "110"), ("buy", day1, "110")):
        session.add(
            AssetTransaction(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                kind=kind,
                quantity=Decimal("10"),
                price=Decimal(price),
                date=when,
            )
        )
    for when, price in ((day0, "100"), (day1, "110")):
        session.add(
            AssetValue(
                asset_id=asset.id,
                workspace_id=test_workspace.id,
                amount=Decimal(price) * 10,
                price=Decimal(price),
                date=when,
                source="market",
            )
        )
    await session.commit()

    result = await get_portfolio_performance_multi(
        session, test_workspace.id, test_user.id, "BRL", [], "3m", end_date=end
    )
    # The ten shares held overnight rose 10% before they were sold; the ten
    # bought back at the close have not moved since.
    assert result.portfolio_return == pytest.approx(10.0)


async def _fund_up_in_january(session: AsyncSession, user: User, workspace) -> None:
    asset = Asset(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name="Manual fund",
        type="fund",
        currency="BRL",
        valuation_method="manual",
    )
    session.add(asset)
    await session.flush()
    session.add_all(
        AssetValue(
            asset_id=asset.id,
            workspace_id=workspace.id,
            amount=Decimal(amount),
            date=when,
            source="manual",
        )
        for when, amount in (
            (date(2026, 1, 1), "100"),
            (date(2026, 1, 31), "120"),
            (date(2026, 6, 30), "120"),
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_a_benchmark_with_a_later_history_does_not_move_the_portfolio_start(
    session: AsyncSession, test_user: User, test_workspace
):
    await _fund_up_in_january(session, test_user, test_workspace)
    end = date(2026, 6, 30)
    late_start = date(2026, 2, 2)
    provider = MultiHistoryProvider(
        {
            "^LATE": [
                BenchmarkHistoryPoint(late_start, 100.0),
                BenchmarkHistoryPoint(date(2026, 4, 1), 105.0),
                BenchmarkHistoryPoint(end, 110.0),
            ]
        }
    )

    alone = await get_portfolio_performance_multi(
        session, test_workspace.id, test_user.id, "BRL", [], "ytd", end_date=end
    )
    compared = await get_portfolio_performance_multi(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        [("yahoo", "^LATE")],
        "ytd",
        end_date=end,
        provider=provider,
    )

    for result in (alone, compared):
        assert result.start_date == date(2026, 1, 1)
        assert result.portfolio_return == pytest.approx(20.0)
        assert result.points[0].date == date(2026, 1, 1)
    metric = compared.benchmarks[0]
    assert metric.start_date == late_start
    assert metric.source_error is None
    assert metric.benchmark_return == pytest.approx(10.0)
    # The portfolio was flat from Feb 2 to Jun 30, so it trails by the full 10%.
    assert metric.excess_return == pytest.approx(-10.0)
    assert compared.benchmark_return == pytest.approx(10.0)
    assert compared.excess_return == pytest.approx(-10.0)
    key = "yahoo:^LATE"
    before = [point for point in compared.points if point.date < late_start]
    assert before and all(key not in point.benchmarks for point in before)
    assert all(point.benchmark is None for point in before)
    on_start = next(point for point in compared.points if point.date == late_start)
    assert on_start.benchmarks[key] == 0.0
    assert on_start.benchmark == 0.0
    assert compared.points[-1].benchmarks[key] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_a_benchmark_with_only_look_back_closes_has_no_data(
    session: AsyncSession, test_user: User, test_workspace
):
    await _fund_up_in_january(session, test_user, test_workspace)
    provider = MultiHistoryProvider(
        {
            "^GONE": [
                BenchmarkHistoryPoint(date(2025, 12, 29), 100.0),
                BenchmarkHistoryPoint(date(2025, 12, 30), 101.0),
            ]
        }
    )
    result = await get_portfolio_performance_multi(
        session,
        test_workspace.id,
        test_user.id,
        "BRL",
        [("yahoo", "^GONE")],
        "ytd",
        end_date=date(2026, 6, 30),
        provider=provider,
    )
    metric = result.benchmarks[0]
    assert metric.source_error == "no_data"
    assert metric.benchmark_return is None
    assert metric.excess_return is None
    assert metric.start_date is None
    assert result.portfolio_return == pytest.approx(20.0)
    assert all(point.benchmarks == {} for point in result.points)
