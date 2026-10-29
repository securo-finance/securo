"""Cash-flow-aware portfolio performance compared with a market benchmark."""

from __future__ import annotations

import asyncio
import calendar
import math
from bisect import bisect_right
from dataclasses import dataclass, field
import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.asset import Asset
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.models.fx_rate import FxRate
from app.providers.benchmark import (
    BenchmarkHistoryPoint,
    BenchmarkHistoryRouter,
    BenchmarkProviderError,
    BenchmarkRateLimitedError,
    get_benchmark_provider,
)
from app.schemas.asset import (
    BenchmarkPerformanceRead,
    BenchmarkSourceError,
    MoneyWeightedSeriesRead,
    PerformanceMethod,
    PortfolioPerformancePoint,
    PortfolioPerformanceRead,
)
from app.services import asset_service
from app.services.fx_rate_service import convert


logger = logging.getLogger(__name__)


_PERIOD_MONTHS = {"3m": 3, "6m": 6, "1y": 12, "3y": 36, "5y": 60}
_SYNCED_FIXED_INCOME_TYPES = {
    "COE",
    "FIXEDINCOME",
    "FIXED_INCOME",
    "RENDA_FIXA",
}
# Unit-price moves beyond this ratio between two snapshots look like a split
# or reverse split, where the share count changes without any cash moving.
_SPLIT_PRICE_RATIO = 0.6


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def period_start(period: str, end_date: date) -> date:
    if period == "ytd":
        return date(end_date.year, 1, 1)
    return _add_months(end_date, -_PERIOD_MONTHS[period])


def _is_synced_fixed_income(asset: Asset) -> bool:
    """Whether a ledger-less holding reports a balance rather than a market price.

    Connected providers expose fixed-income positions as periodic balance
    snapshots. A balance reduction is a redemption/withdrawal, not a market
    loss; treating it as performance makes reallocations look like drawdowns.
    """
    if asset.source == "manual":
        return False
    metadata = asset.external_metadata or {}
    raw_type = metadata.get("type") or metadata.get("investment_type") or ""
    normalized = str(raw_type).strip().upper().replace("-", "_").replace(" ", "_")
    return normalized in _SYNCED_FIXED_INCOME_TYPES


def snapshot_position_flows(
    points: list[tuple[date, float, Optional[float]]],
) -> list[tuple[date, float]]:
    """Cash flows of a position with no trade history, from its share count.

    Fallback for holdings whose provider gave no trades that reconcile.
    ``points`` holds ``(date, value, quantity)`` ascending, one per date. When
    two consecutive snapshots both carry the broker-reported share count, the
    change in quantity priced at the newer unit value is the flow and the rest
    is performance. Snapshots without a share count yield no flow: a value
    change alone never decides whether shares were bought.
    """
    flows: list[tuple[date, float]] = []
    for (_, prev_value, prev_qty), (point_date, value, qty) in zip(points, points[1:]):
        if prev_value <= 0 or value <= 0 or not prev_qty or not qty:
            continue
        unit_value = value / qty
        price_ratio = unit_value / (prev_value / prev_qty)
        if not _SPLIT_PRICE_RATIO <= price_ratio <= 1 / _SPLIT_PRICE_RATIO:
            continue
        flow = (qty - prev_qty) * unit_value
        # Prices are stored to 6 decimals; ignore that rounding noise.
        if abs(flow) <= 1e-4 * value:
            continue
        flows.append((point_date, flow))
    return flows


def snapshot_balance_flows(
    points: list[tuple[date, float, Optional[float]]],
) -> list[tuple[date, float]]:
    """Cash flows of a synced fixed-income holding with no trade history.

    ``points`` holds ``(date, value, quantity)`` ascending, one per date.
    Where two consecutive snapshots both carry a share count, only a change
    in shares moves money, as in ``snapshot_position_flows``: a balance that
    dips and recovers on the same shares is a market move both ways, not a
    redemption followed by a gain. Without a share count the balance alone
    has to decide, and a decrease is taken as a redemption so moving money
    to another holding does not read as a loss.
    """
    flows: list[tuple[date, float]] = []
    for previous, current in zip(points, points[1:]):
        (_, prev_value, prev_qty), (point_date, value, qty) = previous, current
        if prev_qty and qty:
            flows.extend(snapshot_position_flows([previous, current]))
        elif prev_value > value:
            flows.append((point_date, value - prev_value))
    return flows


def snapshot_ledger_flows(
    trades: list[tuple[date, Decimal, Decimal]],
    snapshots: list[tuple[date, float, Optional[float]]],
) -> list[tuple[date, Decimal, date]]:
    """Date each trade of a synced holding where its valuation reflects it.

    ``trades`` holds ``(date, signed quantity, cash flow)``; ``snapshots``
    the provider's valuations ``(date, value, share count or None)``
    ascending. Before the first snapshot the holding is valued from the
    ledger itself, so those trades count on their own date. Afterwards the
    cash must land on the snapshot that first includes the trade, or the
    chart shows a gain or drawdown that is really just timing. Providers
    differ: one day's snapshot already shows a same-day redemption while
    another only shows a purchase the next day. Each trade therefore lands
    on its own date's snapshot if that snapshot holds the post-trade share
    count, and on the next snapshot otherwise. The share count is the stored
    one when known, else the snapshot value at the trade's own price. A day
    whose trades net to no cash has no price to count with, so it waits for
    the next snapshot. Trades no snapshot reflects yet are left out until
    one does. Each flow is returned as ``(snapshot date, cash, trade date)``.
    """
    by_date: dict[date, tuple[Decimal, Decimal]] = {}
    for trade_date, quantity, flow in trades:
        held_quantity, held_flow = by_date.get(trade_date, (Decimal("0"), Decimal("0")))
        by_date[trade_date] = (held_quantity + quantity, held_flow + flow)
    if not snapshots:
        return [
            (trade_date, flow, trade_date) for trade_date, (_, flow) in sorted(by_date.items())
        ]

    first_snapshot = snapshots[0][0]
    position = Decimal("0")
    flows: list[tuple[date, Decimal, date]] = []
    for trade_date, (quantity, flow) in sorted(by_date.items()):
        before, after = position, position + quantity
        position = after
        if trade_date < first_snapshot:
            flows.append((trade_date, flow, trade_date))
            continue
        same_day = next((point for point in snapshots if point[0] == trade_date), None)
        later = next((point for point in snapshots if point[0] > trade_date), None)
        reflected = later
        if same_day is not None:
            _, value, shares = same_day
            if shares is None and quantity and flow:
                shares = value / float(abs(flow) / abs(quantity))
            if shares is not None and abs(shares - float(after)) < abs(shares - float(before)):
                reflected = same_day
        if reflected is not None:
            flows.append((reflected[0], flow, trade_date))
    return flows


async def _first_provider_snapshots(
    session: AsyncSession,
    assets: list[Asset],
    end_date: date,
) -> dict[uuid.UUID, date]:
    """First stored valuation per asset that is not the sync's placeholder."""
    if not assets:
        return {}
    rows = (
        await session.execute(
            select(AssetValue.asset_id, AssetValue.date)
            .where(
                AssetValue.asset_id.in_([asset.id for asset in assets]),
                AssetValue.date <= end_date,
            )
            .order_by(AssetValue.asset_id, AssetValue.date, AssetValue.id)
        )
    ).all()
    dates_by_asset: dict[uuid.UUID, list[date]] = {}
    for asset_id, point_date in rows:
        dates_by_asset.setdefault(asset_id, []).append(point_date)
    firsts: dict[uuid.UUID, date] = {}
    for asset in assets:
        dates = dates_by_asset.get(asset.id, [])
        for index, point_date in enumerate(dates):
            if not asset_service.is_historical_seed(
                asset, point_date, index == 0, index < len(dates) - 1
            ):
                firsts[asset.id] = point_date
                break
    return firsts


async def _snapshot_quantities(
    session: AsyncSession,
    assets: list[Asset],
    end_date: date,
) -> dict[uuid.UUID, dict[date, float]]:
    """Share count per snapshot date, derived from the stored unit price."""
    if not assets:
        return {}
    rows = (
        await session.execute(
            select(AssetValue.asset_id, AssetValue.date, AssetValue.amount, AssetValue.price)
            .where(
                AssetValue.asset_id.in_([asset.id for asset in assets]),
                AssetValue.date <= end_date,
            )
            .order_by(AssetValue.asset_id, AssetValue.date, AssetValue.id)
        )
    ).all()
    quantities: dict[uuid.UUID, dict[date, float]] = {asset.id: {} for asset in assets}
    for asset_id, point_date, amount, price in rows:
        if price is not None and price > 0 and amount > 0:
            quantities[asset_id][point_date] = float(amount / price)
        else:
            # Last write wins: a later price-less row must not inherit a
            # quantity from an earlier row on the same day.
            quantities[asset_id].pop(point_date, None)
    return quantities


async def _asset_observation_starts(
    session: AsyncSession,
    assets: list[Asset],
    end_date: date,
) -> dict[str, date]:
    """Return the first observed valuation date for each asset.

    Pluggy fixed-income imports may contain a lone historical cost seed at
    ``purchase_date`` followed weeks later by the first balance snapshot.
    That seed is useful as a cost basis, but it is not a portfolio valuation:
    treating it as one dilutes all accrued profit when later contributions
    are added before the first sync.
    """
    if not assets:
        return {}
    rows = (
        await session.execute(
            select(
                AssetValue.asset_id,
                AssetValue.date,
                AssetValue.amount,
                AssetValue.source,
            )
            .where(
                AssetValue.asset_id.in_([asset.id for asset in assets]),
                AssetValue.date <= end_date,
            )
            .order_by(AssetValue.asset_id, AssetValue.date, AssetValue.id)
        )
    ).all()
    rows_by_asset: dict[uuid.UUID, list[tuple[date, Decimal, str]]] = {
        asset.id: [] for asset in assets
    }
    for asset_id, point_date, amount, source in rows:
        rows_by_asset[asset_id].append((point_date, amount, source))

    starts: dict[str, date] = {}
    for asset in assets:
        points = rows_by_asset[asset.id]
        later_dates = {point_date for point_date, _, _ in points}
        for point_date, amount, source in points:
            is_historical_seed = (
                _is_synced_fixed_income(asset)
                and source == "sync"
                and asset.purchase_date == point_date
                and asset.purchase_price is not None
                and amount == asset.purchase_price
                and any(other_date > point_date for other_date in later_dates)
            )
            if is_historical_seed:
                continue
            starts[str(asset.id)] = point_date
            break
        else:
            if asset.purchase_date is not None:
                starts[str(asset.id)] = asset.purchase_date
    return starts


def _first_positive_dates(assets: list[Asset], trend_rows: list[dict]) -> dict[str, date]:
    starts: dict[str, date] = {}
    for row in trend_rows:
        point_date = date.fromisoformat(str(row["date"]))
        for asset in assets:
            asset_id = str(asset.id)
            if asset_id not in starts and float(row.get(asset_id, 0.0) or 0.0) > 0:
                starts[asset_id] = point_date
    return starts


def _initial_observation_boundary(
    common_start: date,
    end_date: date,
    first_positive_dates: dict[str, date],
    observation_starts: dict[str, date],
) -> date:
    """Find when every holding introduced so far has an observed valuation."""
    boundary = common_start
    while boundary < end_date:
        next_boundary = boundary
        for asset_id, first_positive in first_positive_dates.items():
            observed = observation_starts.get(asset_id, first_positive)
            if first_positive <= boundary and observed > next_boundary:
                next_boundary = observed
        next_boundary = min(next_boundary, end_date)
        if next_boundary == boundary:
            break
        boundary = next_boundary
    return boundary


def time_weighted_returns(
    values: list[tuple[date, float]],
    contributions: dict[date, float],
    withdrawals: Optional[dict[date, float]] = None,
    *,
    fees: Optional[dict[date, float]] = None,
    opening: Optional[dict[date, float]] = None,
) -> list[tuple[date, float]]:
    """Cumulative time-weighted return, the standard measure brokers report.

    Each interval's return is the change in value net of the money moved in
    or out, and the intervals compound. When or how much money was added
    therefore does not affect the result: the same holdings show the same
    return whether bought in one go or over time.

    ``contributions`` are bought at the price the day is valued at, so the
    day's move up to that price belongs to what was already held, and the
    new money only joins the base afterwards. ``fees`` is the part of them
    that bought nothing (commissions), a loss on the whole portfolio at the
    moment of the buy. ``opening`` holds money bought at the previous
    valuation's price instead, which is exposed to the whole interval and
    counts from its start. Money taken out (sale proceeds, given as
    positive amounts) counts at the end of the day: a sale realises that
    day's price move, so selling everything below the last valuation is a
    loss on what was sold, not a total loss. Without ``withdrawals``,
    negative ``contributions`` are taken as the withdrawals.
    """
    if withdrawals is None:
        withdrawals = {d: -flow for d, flow in contributions.items() if flow < 0}
        contributions = {d: flow for d, flow in contributions.items() if flow > 0}
    fees = fees or {}
    opening = opening or {}
    if not values:
        return []
    growth = 1.0
    previous_value = max(values[0][1], 0.0)
    output = [(values[0][0], 0.0)]
    for point_date, current_value in values[1:]:
        ending = max(current_value, 0.0) + withdrawals.get(point_date, 0.0)
        cash = contributions.get(point_date, 0.0)
        fee = min(fees.get(point_date, 0.0), cash)
        bought = cash - fee
        held = previous_value + opening.get(point_date, 0.0)
        if held > 0 and ending >= bought:
            growth *= (ending - bought) / held
            if fee > 0 and ending > 0:
                growth *= ending / (ending + fee)
        elif held + cash > 0:
            # Nothing held yet (the first buy), or a buy the day's valuation
            # does not cover: measure the day on everything put in.
            growth *= ending / (held + cash)
        # Otherwise nothing was invested over the interval (before the first
        # buy, after selling everything): there is no return to measure.
        previous_value = max(current_value, 0.0)
        output.append((point_date, (growth - 1.0) * 100.0))
    return output


# Bounds on the log of the period's growth: from about -100% to +2e19%.
_MAX_LOG_GROWTH = 45.0
_CASH_EPSILON = 1e-9


def money_weighted_return(
    start_date: date,
    start_value: float,
    flows: list[tuple[date, float]],
    end_date: date,
    end_value: float,
    *,
    guess: float = 0.0,
) -> Optional[float]:
    """Money-weighted return (IRR) over a period, as a cumulative percentage.

    The single rate at which what was held at the start, plus every amount
    put in (``flows`` positive) and less every amount taken out (negative),
    grows into ``end_value``. Unlike the time-weighted return, when and how
    much money moved changes the result: adding money before a rise raises
    it. The rate is compounded over the time money was actually invested,
    from the first money in to the last value held or taken out, so the
    days before the first buy or after selling everything do not stretch
    it. ``guess`` is a starting cumulative percentage for the solver.

    None when no rate balances the money (only possible with money taken
    out and none ever put in).
    """
    cash = [(start_date, -start_value)]
    cash.extend((point_date, -amount) for point_date, amount in flows)
    cash.append((end_date, end_value))
    cash = [(point_date, amount) for point_date, amount in cash if abs(amount) > _CASH_EPSILON]
    paid = sum(-amount for _, amount in cash if amount < 0)
    received = sum(amount for _, amount in cash if amount > 0)
    if not cash:
        return 0.0
    if paid <= 0:
        return None
    if received <= 0:
        return -100.0
    first = min(point_date for point_date, _ in cash)
    span = (max(point_date for point_date, _ in cash) - first).days
    if span == 0:
        # Everything happened on one day: there is no time to compound over.
        return (received / paid - 1.0) * 100.0
    # Solve sum(amount * growth ** -elapsed) = 0 for the period's growth,
    # with elapsed as a fraction of the span, on x = ln(growth).
    timed = [((point_date - first).days / span, amount) for point_date, amount in cash]

    def npv(x: float) -> tuple[float, float]:
        total = slope = 0.0
        for elapsed, amount in timed:
            term = amount * math.exp(-x * elapsed)
            total += term
            slope -= elapsed * term
        return total, slope

    low, high = -_MAX_LOG_GROWTH, _MAX_LOG_GROWTH
    low_value, _ = npv(low)
    high_value, _ = npv(high)
    if low_value == 0:
        return (math.exp(low) - 1.0) * 100.0
    if (low_value > 0) == (high_value > 0):
        return None
    low_positive = low_value > 0
    tolerance = 1e-12 * (paid + received)
    x = min(max(math.log1p(max(guess, -99.9999) / 100.0), low), high)
    for _ in range(200):
        value, slope = npv(x)
        if abs(value) <= tolerance:
            break
        if (value > 0) == low_positive:
            low = x
        else:
            high = x
        # Newton's step, falling back to bisection when it leaves the bracket.
        step = x - value / slope if slope else math.nan
        next_x = step if low < step < high else (low + high) / 2.0
        if abs(next_x - x) < 1e-13:
            x = next_x
            break
        x = next_x
    return (math.exp(x) - 1.0) * 100.0


def money_weighted_returns(
    values: list[tuple[date, float]],
    flows: dict[date, float],
) -> list[tuple[date, float]]:
    """Cumulative money-weighted return from the first value to each later one.

    ``flows`` is the net money put in per date (negative when taken out),
    already reflected in the value on that date. A point no rate can be
    solved for repeats the one before it.
    """
    if not values:
        return []
    start_date, start_value = values[0]
    dated_flows = sorted(
        (point_date, amount)
        for point_date, amount in flows.items()
        if point_date > start_date and amount
    )
    output = [(start_date, 0.0)]
    included: list[tuple[date, float]] = []
    flow_index = 0
    previous = 0.0
    for point_date, value in values[1:]:
        while flow_index < len(dated_flows) and dated_flows[flow_index][0] <= point_date:
            included.append(dated_flows[flow_index])
            flow_index += 1
        result = money_weighted_return(
            start_date, max(start_value, 0.0), included, point_date, max(value, 0.0), guess=previous
        )
        if result is not None:
            previous = result
        output.append((point_date, previous))
    return output


async def _usd_rates(
    session: AsyncSession, currency: str, first: date, last: date
) -> list[tuple[date, float]]:
    """Stored USD→currency rates covering ``first``..``last``, ascending:
    those in the range plus the closest one on either side of it."""
    if currency == "USD":
        return [(first, 1.0)]
    quoted = select(FxRate.date, FxRate.rate).where(
        FxRate.base_currency == "USD", FxRate.quote_currency == currency
    )
    before = (
        await session.execute(
            quoted.where(FxRate.date < first).order_by(FxRate.date.desc()).limit(1)
        )
    ).all()
    within = (
        await session.execute(
            quoted.where(FxRate.date >= first, FxRate.date <= last).order_by(FxRate.date)
        )
    ).all()
    after = (
        await session.execute(quoted.where(FxRate.date > last).order_by(FxRate.date).limit(1))
    ).all()
    return [(point_date, float(rate)) for point_date, rate in [*before, *within, *after]]


async def _in_portfolio_currency(
    session: AsyncSession,
    history: list[BenchmarkHistoryPoint],
    primary_currency: str,
) -> Optional[list[tuple[date, float]]]:
    """A benchmark's values in the portfolio's currency, at each day's rate.

    An index quoted in another currency is not comparable with the
    portfolio until its return includes the currency move, as the
    portfolio's own foreign holdings do. Each day uses the stored rate the
    rest of the app would (that day's, else the closest before, else the
    first after). None when either currency has no stored rate at all; a
    source that does not name its currency is taken as already matching.
    """
    currency = history[0].currency
    pairs = [(point.date, point.value) for point in history]
    if not currency or currency == primary_currency:
        return pairs
    first, last = pairs[0][0], pairs[-1][0]
    source = await _usd_rates(session, currency, first, last)
    target = await _usd_rates(session, primary_currency, first, last)
    if not source or not target:
        return None

    def rate_on(rates: list[tuple[date, float]], target_date: date) -> float:
        index = bisect_right(rates, (target_date, float("inf"))) - 1
        return rates[max(index, 0)][1]

    return [
        (point_date, value * rate_on(target, point_date) / rate_on(source, point_date))
        for point_date, value in pairs
    ]


def _benchmark_index_returns(
    history: list[tuple[date, float]],
    start_date: date,
    end_date: date,
    extra_dates: set[date],
) -> list[tuple[date, float]]:
    """The benchmark's own change since the period start.

    A time-weighted portfolio return is independent of cash flows, so it is
    compared with the index itself rather than a shadow portfolio.
    """
    prices = _benchmark_prices(history, start_date, end_date, extra_dates)
    if not prices:
        return []
    baseline = prices[0][1]
    return [(point_date, (price / baseline - 1.0) * 100.0) for point_date, price in prices]


def _benchmark_prices(
    history: list[tuple[date, float]],
    start_date: date,
    end_date: date,
    extra_dates: set[date],
) -> list[tuple[date, float]]:
    """The benchmark's price as of each date of the period, from its start."""
    baseline = _last_on_or_before(history, start_date)
    if baseline is None:
        baseline = next((point for point in history if point[0] >= start_date), None)
    if baseline is None or baseline[1] <= 0:
        return []

    dates = sorted(
        {start_date, end_date}
        | {point_date for point_date, _ in history if start_date <= point_date <= end_date}
        | {point_date for point_date in extra_dates if start_date <= point_date <= end_date}
    )
    price = baseline[1]
    history_index = 0
    output: list[tuple[date, float]] = []
    for point_date in dates:
        while history_index < len(history) and history[history_index][0] <= point_date:
            if history[history_index][0] >= baseline[0]:
                price = history[history_index][1]
            history_index += 1
        output.append((point_date, price))
    return output


def _benchmark_shadow_portfolio(
    history: list[tuple[date, float]],
    start_date: date,
    end_date: date,
    start_value: float,
    flows: dict[date, float],
    extra_dates: set[date],
) -> tuple[list[tuple[date, float]], dict[date, float]]:
    """The benchmark held with the portfolio's own money, for a money-weighted
    comparison.

    A money-weighted return depends on when money moved, so the index alone
    is not comparable with it. Instead the benchmark is bought with what the
    portfolio held at the start and with every later contribution, at that
    day's price, and sold for every withdrawal. A withdrawal larger than the
    shadow holds (the portfolio did better) only takes out what is there.
    Returns its value per date and the money actually moved.
    """
    prices = _benchmark_prices(history, start_date, end_date, extra_dates | set(flows))
    if not prices:
        return [], {}
    units = max(start_value, 0.0) / prices[0][1]
    values: list[tuple[date, float]] = []
    moved: dict[date, float] = {}
    for point_date, price in prices:
        flow = flows.get(point_date, 0.0) if point_date > start_date else 0.0
        if flow > 0:
            units += flow / price
            moved[point_date] = flow
        elif flow < 0:
            sold = min(-flow, units * price)
            units = max(units - sold / price, 0.0)
            if sold > 0:
                moved[point_date] = -sold
        values.append((point_date, units * price))
    return values, moved


T = TypeVar("T")


def _last_on_or_before(points: list[tuple[date, T]], target: date) -> Optional[tuple[date, T]]:
    result: Optional[tuple[date, T]] = None
    for point in points:
        if point[0] > target:
            break
        result = point
    return result


def _fill_aligned_series(
    portfolio: list[tuple[date, float]],
    benchmark: list[tuple[date, float]],
    start_date: date,
    end_date: date,
) -> list[PortfolioPerformancePoint]:
    return _fill_aligned_series_multi(
        portfolio,
        {"benchmark": benchmark},
        start_date,
        end_date,
    )


def _fill_aligned_series_multi(
    portfolio: list[tuple[date, float]],
    benchmarks: dict[str, list[tuple[date, float]]],
    start_date: date,
    end_date: date,
) -> list[PortfolioPerformancePoint]:
    """Align every series on one union of dates and fill values forward."""
    first_key = next(iter(benchmarks), None)
    dates = sorted(
        {start_date, end_date}
        | {d for d, _ in portfolio if start_date <= d <= end_date}
        | {d for series in benchmarks.values() for d, _ in series if start_date <= d <= end_date}
    )
    points: list[PortfolioPerformancePoint] = []
    portfolio_index = 0
    portfolio_value = 0.0
    benchmark_indexes = {key: 0 for key in benchmarks}
    benchmark_values = {key: 0.0 for key in benchmarks}
    for point_date in dates:
        while portfolio_index < len(portfolio) and portfolio[portfolio_index][0] <= point_date:
            portfolio_value = portfolio[portfolio_index][1]
            portfolio_index += 1
        for key, series in benchmarks.items():
            index = benchmark_indexes[key]
            while index < len(series) and series[index][0] <= point_date:
                benchmark_values[key] = series[index][1]
                index += 1
            benchmark_indexes[key] = index
        rounded_benchmarks = {key: round(value, 6) for key, value in benchmark_values.items()}
        points.append(
            PortfolioPerformancePoint(
                date=point_date,
                portfolio=round(portfolio_value, 6),
                benchmark=rounded_benchmarks.get(first_key or "", 0.0),
                benchmarks=rounded_benchmarks,
            )
        )
    return points


@dataclass
class CashFlows:
    """Money moved into and out of the holdings, per date.

    All amounts are positive and in the primary currency; see
    ``time_weighted_returns`` for how each kind is timed. Contributions and
    withdrawals are kept apart rather than netted so a same-day buy and
    sale still each count at their own end of the day.
    """

    contributions: dict[date, float] = field(default_factory=dict)
    withdrawals: dict[date, float] = field(default_factory=dict)
    fees: dict[date, float] = field(default_factory=dict)
    opening: dict[date, float] = field(default_factory=dict)
    dates: set[date] = field(default_factory=set)

    def net(self) -> dict[date, float]:
        """Money put in less money taken out, per date, for a money-weighted
        return. Every flow counts on the date it is recorded at, the first
        valuation that reflects it."""
        return {
            point_date: (
                self.contributions.get(point_date, 0.0)
                + self.opening.get(point_date, 0.0)
                - self.withdrawals.get(point_date, 0.0)
            )
            for point_date in self.dates
        }


async def _cash_flows(
    session: AsyncSession,
    assets: list[Asset],
    trend_rows: list[dict],
    primary_currency: str,
    start_date: date,
    end_date: date,
    *,
    use_gross_values: bool = False,
) -> CashFlows:
    """Money moved into and out of the holdings, per date."""
    asset_ids = [asset.id for asset in assets]
    tx_rows = list(
        (
            await session.execute(
                select(AssetTransaction)
                .where(AssetTransaction.asset_id.in_(asset_ids))
                .order_by(AssetTransaction.date, AssetTransaction.created_at)
            )
        )
        .scalars()
        .all()
    )
    tx_asset_ids = {tx.asset_id for tx in tx_rows}
    asset_by_id = {asset.id: asset for asset in assets}
    flows = CashFlows()
    rate_cache: dict[tuple[str, date], Decimal] = {}

    async def in_primary(amount: Decimal, currency: str, target_date: date) -> float:
        if currency == primary_currency:
            return float(amount)
        key = (currency, target_date)
        rate = rate_cache.get(key)
        if rate is None:
            _, rate = await convert(
                session,
                Decimal("1"),
                currency,
                primary_currency,
                target_date,
                allow_fetch=False,
            )
            rate_cache[key] = rate
        return float(amount * rate)

    async def add_flow(
        asset: Asset,
        point_date: date,
        native_flow: Decimal,
        *,
        fee: Decimal = Decimal("0"),
        at_open: bool = False,
    ) -> None:
        if not start_date < point_date <= end_date:
            return
        converted = await in_primary(native_flow, asset.currency, point_date)
        converted_fee = await in_primary(fee, asset.currency, point_date) if fee else 0.0
        record(point_date, converted, fee=converted_fee, at_open=at_open)

    def record(point_date: date, amount: float, *, fee: float = 0.0, at_open: bool = False) -> None:
        def add(target: dict[date, float], value: float) -> None:
            target[point_date] = target.get(point_date, 0.0) + value

        if amount > 0 and at_open:
            # Bought at the previous valuation's price: its fee is already
            # part of the base the interval is measured on.
            add(flows.opening, amount)
        elif amount > 0:
            add(flows.contributions, amount)
            if fee > 0:
                add(flows.fees, min(fee, amount))
        elif amount < 0:
            add(flows.withdrawals, -amount)
        flows.dates.add(point_date)

    # Market-priced holdings are valued from the ledger itself, so a trade
    # moves the valuation on its own date. Synced holdings are valued from
    # provider snapshots; their trades are aligned to those below.
    snapshot_ledger_trades: dict[uuid.UUID, list[tuple[date, Decimal, Decimal]]] = {}
    snapshot_ledger_fees: dict[tuple[uuid.UUID, date], Decimal] = {}
    # (bought, cost incl. fees, fees, sold) per market-priced holding and day.
    market_days: dict[tuple[uuid.UUID, date], list[Decimal]] = {}
    for tx in tx_rows:
        asset = asset_by_id.get(tx.asset_id)
        if asset is None:
            continue
        quantity = Decimal(str(tx.quantity))
        gross = quantity * Decimal(str(tx.price))
        fee = Decimal(str(tx.fee or 0))
        native_flow = gross + fee if tx.kind == "buy" else -(gross - fee)
        # A sale's fee already comes off its proceeds.
        buy_fee = fee if tx.kind == "buy" else Decimal("0")
        if asset.valuation_method == "market_price":
            day_totals = market_days.setdefault((asset.id, tx.date), [Decimal("0")] * 4)
            if tx.kind == "buy":
                day_totals[0] += quantity
                day_totals[1] += native_flow
                day_totals[2] += fee
            else:
                day_totals[3] += quantity
                await add_flow(asset, tx.date, native_flow)
        else:
            signed = quantity if tx.kind == "buy" else -quantity
            snapshot_ledger_trades.setdefault(asset.id, []).append((tx.date, signed, native_flow))
            key = (asset.id, tx.date)
            snapshot_ledger_fees[key] = snapshot_ledger_fees.get(key, Decimal("0")) + buy_fee
    for (asset_id, trade_date), (bought, cost, fees, sold) in market_days.items():
        if not bought:
            continue
        # Shares sold again the same day were held through part of it, so
        # that share of the buys counts from the start of the day. The rest
        # is still held at the close and was bought at the day's price.
        round_trip = min(bought, sold) / bought
        asset = asset_by_id[asset_id]
        if round_trip:
            await add_flow(asset, trade_date, cost * round_trip, at_open=True)
        if round_trip < 1:
            kept = 1 - round_trip
            await add_flow(asset, trade_date, cost * kept, fee=fees * kept)

    snapshot_ledger_assets = [asset for asset in assets if asset.id in snapshot_ledger_trades]
    ledger_values = await asset_service._load_asset_native_values(
        session,
        snapshot_ledger_assets,
        up_to_date=end_date,
        use_gross=use_gross_values,
    )
    ledger_quantities = await _snapshot_quantities(session, snapshot_ledger_assets, end_date)
    first_snapshots = await _first_provider_snapshots(session, snapshot_ledger_assets, end_date)
    for asset in snapshot_ledger_assets:
        first_snapshot = first_snapshots.get(asset.id)
        quantities = ledger_quantities[asset.id]
        values_by_date = dict(ledger_values[str(asset.id)])
        snapshots = [
            (point_date, value, quantities.get(point_date))
            for point_date, value in sorted(values_by_date.items())
            if first_snapshot is not None and point_date >= first_snapshot
        ]
        if asset.sell_date is not None:
            # The valuation drops to zero the day after sell_date.
            snapshots = [point for point in snapshots if point[0] <= asset.sell_date]
            snapshots.append((asset.sell_date + timedelta(days=1), 0.0, 0.0))
        for point_date, native_flow, trade_date in snapshot_ledger_flows(
            snapshot_ledger_trades[asset.id], snapshots
        ):
            # A trade only a later snapshot reflects was made at the price
            # of the one before, so it is exposed to the move between them.
            await add_flow(
                asset,
                point_date,
                native_flow,
                fee=snapshot_ledger_fees.get((asset.id, trade_date), Decimal("0")),
                at_open=point_date > trade_date,
            )

    # Connected fixed-income holdings arrive as balance snapshots without a
    # transaction ledger. A redemption or purchase shows in the share count
    # when the provider reports one, and otherwise only as a lower balance
    # (see ``snapshot_balance_flows``).
    synced_fixed_income = [
        asset for asset in assets if asset.id not in tx_asset_ids and _is_synced_fixed_income(asset)
    ]
    native_values = await asset_service._load_asset_native_values(
        session,
        synced_fixed_income,
        up_to_date=end_date,
        use_gross=use_gross_values,
    )
    fixed_income_quantities = await _snapshot_quantities(session, synced_fixed_income, end_date)
    for asset in synced_fixed_income:
        # Last-write-wins for providers that update a same-day snapshot.
        values_by_date = {point_date: value for point_date, value in native_values[str(asset.id)]}
        quantities = fixed_income_quantities[asset.id]
        points = [
            (point_date, value, quantities.get(point_date))
            for point_date, value in sorted(values_by_date.items())
        ]
        for point_date, native_flow in snapshot_balance_flows(points):
            await add_flow(asset, point_date, Decimal(str(native_flow)))

    # Connected market holdings (stocks, ETFs) without a reconciled trade
    # history: buying more shares raises the valuation exactly like a price
    # rally would, so use the reported share count to tell them apart.
    synced_positions = [
        asset
        for asset in assets
        if asset.id not in tx_asset_ids
        and asset.source != "manual"
        and not _is_synced_fixed_income(asset)
    ]
    position_values = await asset_service._load_asset_native_values(
        session,
        synced_positions,
        up_to_date=end_date,
        use_gross=use_gross_values,
    )
    position_quantities = await _snapshot_quantities(session, synced_positions, end_date)
    for asset in synced_positions:
        values_by_date = {point_date: value for point_date, value in position_values[str(asset.id)]}
        quantities = position_quantities[asset.id]
        points = [
            (point_date, value, quantities.get(point_date))
            for point_date, value in sorted(values_by_date.items())
            if asset.sell_date is None or point_date <= asset.sell_date
        ]
        for point_date, native_flow in snapshot_position_flows(points):
            await add_flow(asset, point_date, Decimal(str(native_flow)))

    # Assets without a ledger still need their first appearance treated as an
    # external contribution; otherwise adding a house looks like investment
    # performance. Trend component values are already in primary currency.
    for asset in assets:
        if asset.id in tx_asset_ids:
            continue
        aid = str(asset.id)
        for row in trend_rows:
            point_date = date.fromisoformat(str(row["date"]))
            value = float(row.get(aid, 0.0) or 0.0)
            if value <= 0:
                continue
            if start_date < point_date <= end_date:
                record(point_date, value)
            break

        if asset.sell_date is None or not (start_date <= asset.sell_date < end_date):
            continue
        liquidation_date = asset.sell_date + timedelta(days=1)
        sell_price = asset.sell_price
        if sell_price is None:
            # Sold without a price (left blank, or a provider redemption with
            # no trades): the holding left at its last valuation, rather than
            # vanishing as a total loss.
            proceeds = 0.0
            for row in trend_rows:
                if date.fromisoformat(str(row["date"])) > asset.sell_date:
                    break
                proceeds = float(row.get(aid, 0.0) or 0.0)
        elif sell_price > 0:
            proceeds = await in_primary(Decimal(str(sell_price)), asset.currency, asset.sell_date)
        else:
            # An explicit price of 0 writes the holding off.
            proceeds = 0.0
        if proceeds > 0:
            record(liquidation_date, -proceeds)

    return flows


async def get_portfolio_performance_multi(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    benchmarks: list[tuple[str, str]],
    period: str = "1y",
    asset_group_ids: Optional[list[uuid.UUID]] = None,
    *,
    selected_asset_group_ids: Optional[list[uuid.UUID]] = None,
    asset_ids: Optional[list[uuid.UUID]] = None,
    end_date: Optional[date] = None,
    provider: Optional[BenchmarkHistoryRouter] = None,
    method: PerformanceMethod = "twr",
) -> PortfolioPerformanceRead:
    first_provider = benchmarks[0][0] if benchmarks else None
    first_symbol = benchmarks[0][1] if benchmarks else None
    # The workspace's date, which sync uses to date each snapshot.
    last_date = end_date or app_today()
    requested_start = period_start(period, last_date)
    trend = await asset_service.get_portfolio_trend(
        session,
        workspace_id,
        user_id,
        asset_group_ids=asset_group_ids,
        primary_currency=primary_currency,
        use_gross_values=True,
        selected_asset_group_ids=selected_asset_group_ids,
        asset_ids=asset_ids,
    )
    if len(trend["trend"]) < 2:
        return PortfolioPerformanceRead(
            benchmark_symbol=first_symbol,
            benchmark_provider=first_provider,
            method=method,
            period=period,
            start_date=requested_start,
            end_date=last_date,
            benchmarks=[
                BenchmarkPerformanceRead(
                    key=f"{provider_name}:{symbol}",
                    symbol=symbol,
                    provider=provider_name,
                )
                for provider_name, symbol in benchmarks
            ],
        )

    history_pairs: dict[str, list[tuple[date, float]]] = {}
    source_errors: dict[str, BenchmarkSourceError] = {}
    if benchmarks:
        benchmark_provider = provider or get_benchmark_provider()
        # Include a small look-back so weekends/holidays have an as-of baseline.
        histories = await asyncio.gather(
            *(
                benchmark_provider.history(
                    provider_name,
                    symbol,
                    requested_start - timedelta(days=7),
                    last_date,
                )
                for provider_name, symbol in benchmarks
            ),
            return_exceptions=True,
        )
        for (provider_name, symbol), history in zip(benchmarks, histories, strict=True):
            key = f"{provider_name}:{symbol}"
            if isinstance(history, BenchmarkProviderError):
                source_errors[key] = (
                    "rate_limited"
                    if isinstance(history, BenchmarkRateLimitedError)
                    else "unavailable"
                )
                logger.warning("Benchmark source failed for %s: %s", key, history)
            elif isinstance(history, BaseException):
                raise history
            elif len(history) < 2:
                source_errors[key] = "no_data"
            else:
                converted = await _in_portfolio_currency(session, history, primary_currency)
                if converted is None:
                    source_errors[key] = "no_fx_rate"
                else:
                    history_pairs[key] = converted

    raw_trend: list[dict] = trend["trend"]
    trend_dates = [date.fromisoformat(str(row["date"])) for row in raw_trend]
    earliest_portfolio = trend_dates[0]

    earliest_benchmarks: list[date] = []
    for pairs in history_pairs.values():
        baseline = _last_on_or_before(pairs, requested_start)
        earliest_benchmarks.append(baseline[0] if baseline is not None else pairs[0][0])
    common_start = max(requested_start, earliest_portfolio, *earliest_benchmarks)

    asset_ids = [uuid.UUID(str(meta["id"])) for meta in trend["assets"]]
    first_trade = await session.scalar(
        select(func.min(AssetTransaction.date)).where(AssetTransaction.asset_id.in_(asset_ids))
    )
    inception = earliest_portfolio - timedelta(days=1)
    if (
        common_start == earliest_portfolio
        and requested_start <= inception
        and first_trade == earliest_portfolio
        and all(start <= inception for start in earliest_benchmarks)
    ):
        # The period covers the very first trade: open at zero the day before
        # so those buys count as contributions at what was paid, commissions
        # included, rather than vanishing into a market-valued opening.
        common_start = inception
    assets = list(
        (
            await session.execute(
                select(Asset).where(
                    Asset.workspace_id == workspace_id,
                    Asset.id.in_(asset_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    observation_starts = await _asset_observation_starts(session, assets, last_date)
    first_positive_dates = _first_positive_dates(assets, raw_trend)
    observation_boundary = _initial_observation_boundary(
        common_start,
        last_date,
        first_positive_dates,
        observation_starts,
    )
    flows = await _cash_flows(
        session,
        assets,
        raw_trend,
        primary_currency,
        common_start,
        last_date,
        use_gross_values=True,
    )

    rows_by_date = {date.fromisoformat(str(row["date"])): row for row in raw_trend}

    def snapshot(target: date) -> float:
        # The trend's dates are ascending: use its last row on or before target.
        index = bisect_right(trend_dates, target)
        if index == 0:
            return 0.0
        row = rows_by_date[trend_dates[index - 1]]
        total = 0.0
        for asset in assets:
            value = float(row.get(str(asset.id), 0.0) or 0.0)
            if asset.sell_date is not None and target > asset.sell_date:
                value = 0.0
            total += value
        return total

    portfolio_dates = (
        {common_start, observation_boundary, last_date}
        | {d for d in trend_dates if observation_boundary < d <= last_date}
        | {d for d in flows.dates if common_start < d <= last_date}
    )
    portfolio_values = [(d, snapshot(d)) for d in sorted(portfolio_dates)]
    net_flows = flows.net()
    if method == "mwr":
        portfolio_returns = money_weighted_returns(portfolio_values, net_flows)
    else:
        portfolio_returns = time_weighted_returns(
            portfolio_values,
            flows.contributions,
            flows.withdrawals,
            fees=flows.fees,
            opening=flows.opening,
        )
    benchmark_returns: dict[str, list[tuple[date, float]]] = {}
    shadows: dict[str, tuple[list[tuple[date, float]], dict[date, float]]] = {}
    for key, pairs in history_pairs.items():
        if method == "mwr":
            shadow_values, shadow_flows = _benchmark_shadow_portfolio(
                pairs,
                common_start,
                last_date,
                portfolio_values[0][1],
                net_flows,
                portfolio_dates,
            )
            returns = money_weighted_returns(shadow_values, shadow_flows)
            if returns:
                shadows[key] = (shadow_values, shadow_flows)
        else:
            returns = _benchmark_index_returns(
                pairs,
                common_start,
                last_date,
                portfolio_dates,
            )
        if returns:
            benchmark_returns[key] = returns
        else:
            source_errors[key] = "no_data"

    points = _fill_aligned_series_multi(
        portfolio_returns,
        benchmark_returns,
        common_start,
        last_date,
    )
    if benchmarks:
        # The legacy scalar still refers to the first requested benchmark.
        # Never substitute a different successful benchmark for a failed one.
        for point in points:
            point.benchmark = point.benchmarks.get(f"{first_provider}:{first_symbol}", 0.0)
    portfolio_return = points[-1].portfolio if points else None
    benchmark_metrics: list[BenchmarkPerformanceRead] = []
    for provider_name, symbol in benchmarks:
        key = f"{provider_name}:{symbol}"
        benchmark_return = points[-1].benchmarks.get(key) if points else None
        excess_return = (
            portfolio_return - benchmark_return
            if portfolio_return is not None and benchmark_return is not None
            else None
        )
        benchmark_metrics.append(
            BenchmarkPerformanceRead(
                key=key,
                symbol=symbol,
                provider=provider_name,
                benchmark_return=(
                    round(benchmark_return, 6) if benchmark_return is not None else None
                ),
                excess_return=(round(excess_return, 6) if excess_return is not None else None),
                source_error=source_errors.get(key),
            )
        )
    first_metric = benchmark_metrics[0] if benchmark_metrics else None
    return PortfolioPerformanceRead(
        benchmark_symbol=first_symbol,
        benchmark_provider=first_provider,
        method=method,
        period=period,
        start_date=common_start,
        end_date=last_date,
        portfolio_return=round(portfolio_return, 6) if portfolio_return is not None else None,
        benchmark_return=first_metric.benchmark_return if first_metric else None,
        excess_return=first_metric.excess_return if first_metric else None,
        benchmarks=benchmark_metrics,
        points=points,
        money_weighted=(
            _money_weighted_series(points, portfolio_values, net_flows, shadows)
            if method == "mwr"
            else None
        ),
    )


def _money_weighted_series(
    points: list[PortfolioPerformancePoint],
    portfolio_values: list[tuple[date, float]],
    net_flows: dict[date, float],
    shadows: dict[str, tuple[list[tuple[date, float]], dict[date, float]]],
) -> MoneyWeightedSeriesRead:
    """Each point's value and money moved, so a range can be re-solved."""
    point_dates = [point.date for point in points]

    def aligned(
        values: list[tuple[date, float]], moved: dict[date, float]
    ) -> tuple[list[float], list[float]]:
        # Values carry forward to dates only a benchmark has; money moved
        # on the first date is already part of what was held then.
        held: list[float] = []
        value = 0.0
        value_index = 0
        for point_date in point_dates:
            while value_index < len(values) and values[value_index][0] <= point_date:
                value = values[value_index][1]
                value_index += 1
            held.append(round(value, 2))
        flows = [0.0] + [round(moved.get(point_date, 0.0), 2) for point_date in point_dates[1:]]
        return held, flows

    values, flows = aligned(portfolio_values, net_flows)
    series = MoneyWeightedSeriesRead(values=values, flows=flows)
    for key, (shadow_values, shadow_flows) in shadows.items():
        held, moved = aligned(shadow_values, shadow_flows)
        series.benchmark_values[key] = held
        series.benchmark_flows[key] = moved
    return series


async def get_portfolio_performance(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    primary_currency: str,
    benchmark_provider_name: str,
    benchmark_symbol: str,
    period: str = "1y",
    asset_group_ids: Optional[list[uuid.UUID]] = None,
    *,
    selected_asset_group_ids: Optional[list[uuid.UUID]] = None,
    asset_ids: Optional[list[uuid.UUID]] = None,
    end_date: Optional[date] = None,
    provider: Optional[BenchmarkHistoryRouter] = None,
    method: PerformanceMethod = "twr",
) -> PortfolioPerformanceRead:
    """Backward-compatible single-benchmark entry point."""
    return await get_portfolio_performance_multi(
        session,
        workspace_id,
        user_id,
        primary_currency,
        [(benchmark_provider_name, benchmark_symbol)],
        period,
        asset_group_ids,
        selected_asset_group_ids=selected_asset_group_ids,
        asset_ids=asset_ids,
        end_date=end_date,
        provider=provider,
        method=method,
    )
