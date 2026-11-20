"""Cash-flow-aware portfolio performance compared with a market benchmark."""

from __future__ import annotations

import asyncio
import calendar
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple, Optional, TypeVar

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
# Amounts are money: anything this small left after a sale is float residue.
_HELD_DUST = 1e-6
# How long a snapshot without a share count may take to show a trade (a
# weekend, a holiday, settlement) before the trade is dated the old way.
_SHOWN_WITHIN = timedelta(days=7)


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
    another only shows a purchase days later.

    Trades are shown in the order they were made, so each snapshot is
    matched to the ledger state, among those its pending trades lead
    through, closest to its share count, and the trades up to that state
    land on it. Trades that leave and return to that count (a buy sold
    again) wait if the next snapshots show a state in between, as a
    provider running behind would. The share count is the stored one;
    without it, a snapshot on a trade's own date is counted at that day's
    trade price, and a later one takes the trades whose cash best matches
    its change in value since the previous snapshot; one no such snapshot
    shows within a week lands on the snapshot after it. A day
    whose trades leave the share count unchanged (sell and buy back) lands
    on its own day's snapshot, as no count can show it. Trades no snapshot
    has caught up with yet are left out until one does. Each flow is
    returned as ``(snapshot date, cash, trade date)``.
    """
    by_date: dict[date, tuple[Decimal, Decimal]] = {}
    for trade_date, quantity, flow in trades:
        held_quantity, held_flow = by_date.get(trade_date, (Decimal("0"), Decimal("0")))
        by_date[trade_date] = (held_quantity + quantity, held_flow + flow)
    days = sorted(by_date.items())
    if not snapshots:
        return [(trade_date, flow, trade_date) for trade_date, (_, flow) in days]

    first_snapshot = snapshots[0][0]
    flows: list[tuple[date, Decimal, date]] = []
    position = Decimal("0")
    pending: list[tuple[date, Decimal, Decimal]] = []
    for trade_date, (quantity, flow) in days:
        if trade_date < first_snapshot:
            flows.append((trade_date, flow, trade_date))
            position += quantity
        else:
            pending.append((trade_date, quantity, flow))

    def land(count: int, on: Optional[date] = None) -> None:
        nonlocal position
        for trade_date, quantity, flow in pending[:count]:
            target = on or next((point[0] for point in snapshots if point[0] > trade_date), None)
            if target is not None:
                flows.append((target, flow, trade_date))
            position += quantity
        del pending[:count]

    previous_value: Optional[float] = None
    for snapshot_index, (point_date, value, shares) in enumerate(snapshots):
        change = None if previous_value is None else value - previous_value
        previous_value = value
        eligible = 0
        while eligible < len(pending) and pending[eligible][0] <= point_date:
            eligible += 1
        if not eligible:
            continue
        last_date, last_quantity, last_flow = pending[eligible - 1]
        if shares is None and last_date == point_date and last_quantity and last_flow:
            shares = value / float(abs(last_flow) / abs(last_quantity))
        if shares is None:
            # No count to tell by: trades before this date land here, and so
            # does a same-day round trip, which no count could show. Trades
            # are merged per day, so only the last one can be on this date.
            landed = eligible if last_date < point_date or not last_quantity else eligible - 1
            if last_date < point_date and change is not None:
                # A provider running behind (a weekend, settlement) shows the
                # trade as a move in value the size of its cash. Until one
                # does, wait; a trade never seen that way within a week is
                # dated on the snapshot after it, as before.
                overdue = 0
                while overdue < eligible and point_date - pending[overdue][0] > _SHOWN_WITHIN:
                    overdue += 1
                land(overdue)
                eligible -= overdue
                landed, shown, cash = 0, abs(change), 0.0
                for index, (_, _, flow) in enumerate(pending[:eligible], start=1):
                    cash += float(flow)
                    # Closer wins; an exact match also wins a tie, so trades
                    # whose cash nets to the change (a round trip against no
                    # move) are shown by it. A tie that is only as far off as
                    # showing nothing is not a match.
                    residual = abs(change - cash)
                    if residual < shown or residual == 0:
                        landed, shown = index, residual
        else:
            state = position
            states = [state]
            first_best, best, best_state = 0, 0, state
            best_distance = abs(shares - float(state))
            for index, (_, quantity, _) in enumerate(pending[:eligible], start=1):
                state += quantity
                states.append(state)
                distance = abs(shares - float(state))
                if distance < best_distance:
                    first_best, best, best_state, best_distance = index, index, state, distance
                elif distance == best_distance and state == best_state:
                    # Trades that leave the count where it was are shown as
                    # much as the state before them: take them along.
                    best = index
            between = [float(item) for item in states[first_best + 1 : best] if item != best_state]
            if between:
                # Trades that went away from this count and came back. A
                # provider running behind shows the states in between on its
                # next snapshots; then none of them is shown yet.
                next_trade = pending[eligible][0] if eligible < len(pending) else None
                for later_date, _, later_shares in snapshots[snapshot_index + 1 :]:
                    if later_shares is None or (next_trade is not None and later_date >= next_trade):
                        break
                    back = abs(later_shares - float(best_state))
                    if any(abs(later_shares - item) < back for item in between):
                        best = first_best
                        break
            landed = best
        land(landed, point_date)

    # Trades still pending are not in any valuation yet: counting their cash
    # now would read the missing value as a loss or a gain. They count once a
    # snapshot shows them.
    flows.sort(key=lambda item: (item[0], item[2]))
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


class IntradayTrade(NamedTuple):
    """A trade at a known price, splitting its day into sub-periods.

    ``revalue`` is the portfolio's move from the last mark up to the trade's
    price, ``amount`` the value bought (positive) or sold (negative) at that
    price, and ``fee`` the commission, all in the primary currency.
    """

    revalue: float
    amount: float
    fee: float = 0.0


def time_weighted_returns(
    values: list[tuple[date, float]],
    contributions: dict[date, float],
    withdrawals: Optional[dict[date, float]] = None,
    *,
    fees: Optional[dict[date, float]] = None,
    opening: Optional[dict[date, float]] = None,
    intraday: Optional[dict[date, list[IntradayTrade]]] = None,
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

    ``intraday`` lists trades made at a known price, in the order they were
    made. Each one ends a sub-period: what was held is marked to the trade's
    price, and the trade then joins or leaves the base at that price. The
    rest of the day, up to the valuation, is measured on what is left.
    """
    if withdrawals is None:
        withdrawals = {d: -flow for d, flow in contributions.items() if flow < 0}
        contributions = {d: flow for d, flow in contributions.items() if flow > 0}
    fees = fees or {}
    opening = opening or {}
    intraday = intraday or {}
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
        for trade in intraday.get(point_date, ()):
            marked = max(held + trade.revalue, 0.0)
            if held > 0:
                # Marked to zero (the only holding sold for nothing) is a
                # total loss, and the return stays there.
                growth *= marked / held
            held = marked
            if trade.amount >= 0:
                held += trade.amount
                if trade.fee > 0 and held > 0:
                    # A commission bought nothing: a loss on everything held.
                    growth *= held / (held + trade.fee)
            else:
                if trade.fee > 0 and held > 0:
                    growth *= max(held - trade.fee, 0.0) / held
                held = max(held + trade.amount, 0.0)
            if held < _HELD_DUST:
                # Selling everything leaves float residue, not a holding.
                held = 0.0
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
    on_dates: Iterable[date] = (),
) -> Optional[list[tuple[date, float]]]:
    """A benchmark's values in the portfolio's currency, at each day's rate.

    An index quoted in another currency is not comparable with the
    portfolio until its return includes the currency move, as the
    portfolio's own foreign holdings do. Each day uses the stored rate the
    rest of the app would (that day's, else the closest before, else the
    first after). None when either currency has no stored rate at all; a
    source that does not name its currency is taken as already matching.

    ``on_dates`` are the other dates the benchmark is compared on. A date
    with no close carries the last close forward in the index's own
    currency and converts it at that date's rate, as a foreign holding
    valued that day would be; carrying the converted value instead would
    leave the currency move since the close out of the benchmark only.
    """
    currency = history[0].currency
    pairs = [(point.date, point.value) for point in history]
    if not currency or currency == primary_currency:
        return pairs
    close_dates = [point_date for point_date, _ in pairs]
    known = set(close_dates)
    carried = [
        (point_date, pairs[bisect_right(close_dates, point_date) - 1][1])
        for point_date in sorted(set(on_dates))
        if point_date > close_dates[0] and point_date not in known
    ]
    pairs = sorted(pairs + carried)
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
        output.append((point_date, (price / baseline[1] - 1.0) * 100.0))
    return output


T = TypeVar("T")


def _last_on_or_before(points: list[tuple[date, T]], target: date) -> Optional[tuple[date, T]]:
    result: Optional[tuple[date, T]] = None
    for point in points:
        if point[0] > target:
            break
        result = point
    return result


def _return_since(cumulative: list[tuple[date, float]], since: date) -> Optional[float]:
    """The return from ``since`` to the last point of a cumulative series.

    Cumulative time-weighted returns compound, so the part after ``since``
    is the ratio of the growth factors, using the value as of that date.
    None when nothing was left to grow from (a total loss by then).
    """
    if not cumulative:
        return None
    as_of = _last_on_or_before(cumulative, since)
    growth_before = 1.0 + (as_of[1] if as_of is not None else 0.0) / 100.0
    if growth_before <= 0:
        return None
    return ((1.0 + cumulative[-1][1] / 100.0) / growth_before - 1.0) * 100.0


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
    """Align every series on one union of dates and fill values forward.

    A benchmark only appears from its own first date on: before it, the
    point carries no value for it rather than a made-up 0.
    """
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
    benchmark_values: dict[str, float] = {}
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
                benchmark=rounded_benchmarks.get(first_key) if first_key else None,
                benchmarks=rounded_benchmarks,
            )
        )
    return points


class MarketTrade(NamedTuple):
    """A trade in a market-priced holding, in the primary currency.

    ``mark`` is the holding marked to the trade's price just before it, and
    ``amount`` the value bought (positive) or sold (negative) at that price.
    A trade at no price says nothing about what the holding is worth, so
    its ``mark`` is None: a buy (free shares, such as a split recorded as a
    buy) leaves the holding's value as it was, and a sale (a write-off)
    takes ``written_off``, the share of the holding sold, out of it.
    """

    asset_key: str
    mark: Optional[float]
    amount: float
    fee: float
    written_off: float = 0.0


@dataclass
class CashFlows:
    """Money moved into and out of the holdings, per date.

    All amounts are positive and in the primary currency; see
    ``time_weighted_returns`` for how each kind is timed. Contributions and
    withdrawals are kept apart rather than netted so a same-day buy and
    sale still each count at their own end of the day. ``trades`` holds the
    market-priced trades in ledger order, which split their day at each
    trade's price instead.
    """

    contributions: dict[date, float] = field(default_factory=dict)
    withdrawals: dict[date, float] = field(default_factory=dict)
    fees: dict[date, float] = field(default_factory=dict)
    opening: dict[date, float] = field(default_factory=dict)
    trades: dict[date, list[MarketTrade]] = field(default_factory=dict)
    dates: set[date] = field(default_factory=set)


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
                .order_by(AssetTransaction.date, AssetTransaction.created_at, AssetTransaction.id)
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

    # Market-priced holdings are valued from the ledger itself, and each
    # trade has its own price, so a trade splits its day there (see
    # ``time_weighted_returns``). Synced holdings are valued from provider
    # snapshots; their trades are aligned to those below.
    snapshot_ledger_trades: dict[uuid.UUID, list[tuple[date, Decimal, Decimal]]] = {}
    snapshot_ledger_fees: dict[tuple[uuid.UUID, date], Decimal] = {}
    positions: dict[uuid.UUID, Decimal] = {}
    # Replay in ledger order: a day's split before that day's trades. Trades
    # saved together (an import) share a timestamp and have no order of
    # their own. There, sales of other holdings go first, as they fund the
    # buys, and a holding's own buys precede its sales, so a sale never
    # comes before the buy that gave it the shares.
    batch_buys = {(tx.asset_id, tx.date, tx.created_at) for tx in tx_rows if tx.kind == "buy"}

    def ledger_order(tx: AssetTransaction) -> tuple:
        if tx.kind == "buy":
            rank = 1
        elif (tx.asset_id, tx.date, tx.created_at) in batch_buys:
            rank = 2
        else:
            rank = 0
        return (tx.date, tx.kind != "split", tx.created_at is None, tx.created_at, rank)

    tx_rows.sort(key=ledger_order)
    for tx in tx_rows:
        asset = asset_by_id.get(tx.asset_id)
        if asset is None:
            continue
        if tx.kind == "split":
            # A split changes the units held, not the money in the holding.
            if tx.old_units and tx.new_units:
                positions[asset.id] = asset_service.split_units(
                    positions.get(asset.id, Decimal("0")), tx.old_units, tx.new_units
                )
            continue
        quantity = Decimal(str(tx.quantity))
        gross = quantity * Decimal(str(tx.price))
        fee = Decimal(str(tx.fee or 0))
        native_flow = gross + fee if tx.kind == "buy" else -(gross - fee)
        # A sale's fee already comes off its proceeds.
        buy_fee = fee if tx.kind == "buy" else Decimal("0")
        if asset.valuation_method == "market_price":
            held_before = positions.get(asset.id, Decimal("0"))
            positions[asset.id] = held_before + (quantity if tx.kind == "buy" else -quantity)
            if start_date < tx.date <= end_date:
                price = Decimal(str(tx.price))
                written_off = 0.0
                if price > 0:
                    mark = await in_primary(
                        max(held_before, Decimal("0")) * price, asset.currency, tx.date
                    )
                else:
                    mark = None
                    if tx.kind == "sell" and held_before > 0:
                        written_off = float(min(quantity / held_before, Decimal("1")))
                amount = await in_primary(gross, asset.currency, tx.date)
                converted_fee = await in_primary(fee, asset.currency, tx.date) if fee else 0.0
                flows.trades.setdefault(tx.date, []).append(
                    MarketTrade(
                        str(asset.id),
                        mark,
                        amount if tx.kind == "buy" else -amount,
                        converted_fee,
                        written_off,
                    )
                )
                flows.dates.add(tx.date)
        else:
            signed = quantity if tx.kind == "buy" else -quantity
            snapshot_ledger_trades.setdefault(asset.id, []).append((tx.date, signed, native_flow))
            key = (asset.id, tx.date)
            snapshot_ledger_fees[key] = snapshot_ledger_fees.get(key, Decimal("0")) + buy_fee
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

    native_histories: dict[str, list[BenchmarkHistoryPoint]] = {}
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
                native_histories[key] = history

    raw_trend: list[dict] = trend["trend"]
    trend_dates = [date.fromisoformat(str(row["date"])) for row in raw_trend]
    earliest_portfolio = trend_dates[0]
    # The portfolio's own period. A benchmark never moves it: one with a
    # shorter history is compared from its own start instead (see below).
    portfolio_start = max(requested_start, earliest_portfolio)

    asset_ids = [uuid.UUID(str(meta["id"])) for meta in trend["assets"]]
    first_trade = await session.scalar(
        select(func.min(AssetTransaction.date)).where(AssetTransaction.asset_id.in_(asset_ids))
    )
    inception = earliest_portfolio - timedelta(days=1)
    if (
        portfolio_start == earliest_portfolio
        and requested_start <= inception
        and first_trade == earliest_portfolio
    ):
        # The period covers the very first trade: open at zero the day before
        # so those buys count as contributions at what was paid, commissions
        # included, rather than vanishing into a market-valued opening.
        portfolio_start = inception
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
        portfolio_start,
        last_date,
        first_positive_dates,
        observation_starts,
    )
    flows = await _cash_flows(
        session,
        assets,
        raw_trend,
        primary_currency,
        portfolio_start,
        last_date,
        use_gross_values=True,
    )

    rows_by_date = {date.fromisoformat(str(row["date"])): row for row in raw_trend}

    def asset_value(asset: Asset, target: date) -> float:
        # The trend's dates are ascending: use its last row on or before target.
        index = bisect_right(trend_dates, target)
        if index == 0:
            return 0.0
        if asset.sell_date is not None and target > asset.sell_date:
            return 0.0
        row = rows_by_date[trend_dates[index - 1]]
        return float(row.get(str(asset.id), 0.0) or 0.0)

    def snapshot(target: date) -> float:
        return sum(asset_value(asset, target) for asset in assets)

    portfolio_dates = (
        {portfolio_start, observation_boundary, last_date}
        | {d for d in trend_dates if observation_boundary < d <= last_date}
        | {d for d in flows.dates if portfolio_start < d <= last_date}
    )
    sorted_dates = sorted(portfolio_dates)
    portfolio_values = [(d, snapshot(d)) for d in sorted_dates]
    asset_by_key = {str(asset.id): asset for asset in assets}
    intraday: dict[date, list[IntradayTrade]] = {}
    for previous_date, point_date in zip(sorted_dates, sorted_dates[1:]):
        # Each holding starts the day at its previous valuation and is then
        # marked at each of its trades' prices, in ledger order.
        marks: dict[str, float] = {}
        for trade in flows.trades.get(point_date, ()):
            last_mark = marks.get(trade.asset_key)
            if last_mark is None:
                last_mark = asset_value(asset_by_key[trade.asset_key], previous_date)
            mark = last_mark * (1.0 - trade.written_off) if trade.mark is None else trade.mark
            intraday.setdefault(point_date, []).append(
                IntradayTrade(mark - last_mark, trade.amount, trade.fee)
            )
            marks[trade.asset_key] = mark + trade.amount
    portfolio_returns = time_weighted_returns(
        portfolio_values,
        flows.contributions,
        flows.withdrawals,
        fees=flows.fees,
        opening=flows.opening,
        intraday=intraday,
    )
    benchmark_returns: dict[str, list[tuple[date, float]]] = {}
    benchmark_starts: dict[str, date] = {}
    for key, history in native_histories.items():
        pairs = await _in_portfolio_currency(
            session, history, primary_currency, on_dates=portfolio_dates
        )
        if pairs is None:
            source_errors[key] = "no_fx_rate"
            continue
        close_dates = [point.date for point in history]
        # Compared from the portfolio's start when the benchmark has a close
        # on or before it (the look-back covers weekends and holidays), else
        # from its own first close.
        baseline = _last_on_or_before(pairs, portfolio_start)
        benchmark_start = max(portfolio_start, baseline[0] if baseline is not None else pairs[0][0])
        # Closes from the look-back alone would carry the last one forward
        # as a flat 0%: the benchmark needs a close inside its own window.
        if not any(benchmark_start < point_date <= last_date for point_date in close_dates):
            source_errors[key] = "no_data"
            continue
        returns = _benchmark_index_returns(
            pairs,
            benchmark_start,
            last_date,
            portfolio_dates,
        )
        if returns:
            benchmark_returns[key] = returns
            benchmark_starts[key] = benchmark_start
        else:
            source_errors[key] = "no_data"

    points = _fill_aligned_series_multi(
        portfolio_returns,
        benchmark_returns,
        portfolio_start,
        last_date,
    )
    primary_key = f"{first_provider}:{first_symbol}" if benchmarks else None
    for point in points:
        # The legacy scalar still refers to the first requested benchmark.
        # Never substitute a different successful benchmark for a failed one.
        point.benchmark = point.benchmarks.get(primary_key) if primary_key else None
    portfolio_return = portfolio_returns[-1][1] if portfolio_returns else None
    benchmark_metrics: list[BenchmarkPerformanceRead] = []
    for provider_name, symbol in benchmarks:
        key = f"{provider_name}:{symbol}"
        series = benchmark_returns.get(key)
        benchmark_return = series[-1][1] if series else None
        benchmark_start = benchmark_starts.get(key)
        excess_return = None
        if benchmark_return is not None and benchmark_start is not None:
            window_return = _return_since(portfolio_returns, benchmark_start)
            if window_return is not None:
                excess_return = window_return - benchmark_return
        benchmark_metrics.append(
            BenchmarkPerformanceRead(
                key=key,
                symbol=symbol,
                provider=provider_name,
                start_date=benchmark_start,
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
        period=period,
        start_date=portfolio_start,
        end_date=last_date,
        portfolio_return=round(portfolio_return, 6) if portfolio_return is not None else None,
        benchmark_return=first_metric.benchmark_return if first_metric else None,
        excess_return=first_metric.excess_return if first_metric else None,
        benchmarks=benchmark_metrics,
        points=points,
    )


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
    )
