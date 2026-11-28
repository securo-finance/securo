"""Money-weighted return (IRR) for portfolio performance.

The time-weighted return in ``portfolio_performance_service`` answers how
the holdings did; this answers how the money did, counting when it was put
in and taken out. The chart's line and any range the client selects on it
are solved from the same per-point values and money moved, so dragging from
the start of the chart to a point reads exactly the line's value there.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date
from typing import NamedTuple, Optional

from app.schemas.asset import MoneyWeightedSeriesRead, PortfolioPerformancePoint


# Bounds on the log of the period's growth: from about -100% to +2e19%.
_MAX_LOG_GROWTH = 45.0
_CASH_EPSILON = 1e-9

# Money put in and money taken out on one date, both positive and kept
# apart: a buy and a sale on the same day are two amounts, not their net.
Legs = tuple[float, float]


def _entries(dated_legs: Iterable[tuple[date, Legs]]) -> list[tuple[date, float]]:
    """Legs as the signed amounts the solver takes, money in first."""
    entries: list[tuple[date, float]] = []
    for point_date, (put_in, taken_out) in dated_legs:
        if put_in:
            entries.append((point_date, put_in))
        if taken_out:
            entries.append((point_date, -taken_out))
    return entries


def _sign_changes(cash: list[tuple[date, float]]) -> int:
    """How often the cash changes sign over time, each date's amounts summed."""
    totals: dict[date, float] = {}
    for point_date, amount in cash:
        totals[point_date] = totals.get(point_date, 0.0) + amount
    signs = [amount > 0 for _, amount in sorted(totals.items()) if abs(amount) > _CASH_EPSILON]
    return sum(1 for before, after in zip(signs, signs[1:]) if before != after)


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
    it. ``guess`` is a starting cumulative percentage for the solver; it is
    ignored when the cash could have more than one rate (below).

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
    if _sign_changes(cash) > 1:
        # Money taken out and put in again can balance at more than one rate
        # (no more than the number of sign changes). Which one the solver
        # finds depends on where it starts, so it always starts from 0 here:
        # the line and a range over the same cash, here and in the client,
        # then land on the same rate.
        guess = 0.0
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

    tolerance = 1e-12 * (paid + received)
    start = min(max(math.log1p(max(guess, -99.9999) / 100.0), -_MAX_LOG_GROWTH), _MAX_LOG_GROWTH)
    # From a nearby guess (the previous point of a series), Newton's method
    # alone usually lands in a step or two; the bracket below, which costs
    # two more passes over the cash, is only for when it does not.
    x = start
    for _ in range(8):
        value, slope = npv(x)
        if abs(value) <= tolerance:
            return (math.exp(x) - 1.0) * 100.0
        if not slope:
            break
        step = value / slope
        x -= step
        if not -_MAX_LOG_GROWTH < x < _MAX_LOG_GROWTH:
            break
        if abs(step) < 1e-13:
            # Converged to within the last digits a further pass would add.
            return (math.exp(x) - 1.0) * 100.0
    low, high = -_MAX_LOG_GROWTH, _MAX_LOG_GROWTH
    low_value, _ = npv(low)
    high_value, _ = npv(high)
    if low_value == 0:
        return (math.exp(low) - 1.0) * 100.0
    if (low_value > 0) == (high_value > 0):
        return None
    low_positive = low_value > 0
    x = start
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
    flows: dict[date, Legs],
) -> list[tuple[date, float]]:
    """Cumulative money-weighted return from the first value to each later one.

    ``flows`` is the money put in and taken out per date, already reflected
    in the value on that date. A point no rate can be solved for repeats the
    one before it.
    """
    if not values:
        return []
    start_date, start_value = values[0]
    dated_flows = _entries(
        sorted((point_date, legs) for point_date, legs in flows.items() if point_date > start_date)
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


def range_return(
    dates: list[date], values: list[float], flows: list[Legs], start: int, end: int
) -> Optional[float]:
    """The return between two points of an aligned series.

    What was held at ``start``, plus the money moved after it up to
    ``end``, grown into what was held at ``end``. The client's
    ``moneyWeightedReturn`` solves a dragged range the same way.
    """
    moved = _entries((dates[index], flows[index]) for index in range(start + 1, end + 1))
    return money_weighted_return(
        dates[start], max(values[start], 0.0), moved, dates[end], max(values[end], 0.0)
    )


def _aligned(
    point_dates: list[date], values: list[tuple[date, float]], moved: dict[date, Legs]
) -> tuple[list[float], list[Legs]]:
    """A series' value and money moved on each of the chart's dates.

    Values carry forward to dates only another series has. Money moved on
    the first date is already part of what was held then. A series that
    starts after the chart (a benchmark with a later first close) counts
    its first value as money in, so a range from before it solves as one
    that starts with it.
    """
    held: list[float] = []
    flows: list[Legs] = []
    value = 0.0
    value_index = 0
    for index, point_date in enumerate(point_dates):
        started = value_index > 0
        while value_index < len(values) and values[value_index][0] <= point_date:
            value = values[value_index][1]
            value_index += 1
        held.append(round(value, 6))
        put_in, taken_out = moved.get(point_date, (0.0, 0.0)) if index else (0.0, 0.0)
        if index and not started and value_index:
            put_in += value
        flows.append((round(put_in, 6), round(taken_out, 6)))
    return held, flows


class SolvedSeries(NamedTuple):
    series: MoneyWeightedSeriesRead
    portfolio: list[tuple[date, float]]
    benchmarks: dict[str, list[tuple[date, float]]]
    # The portfolio's return over each benchmark's own window.
    windows: dict[str, Optional[float]]


def solve_on_points(
    points: list[PortfolioPerformancePoint],
    portfolio_values: list[tuple[date, float]],
    money_flows: dict[date, Legs],
    shadows: dict[str, tuple[list[tuple[date, float]], dict[date, Legs]]],
    benchmark_starts: dict[str, date],
) -> SolvedSeries:
    """Solve the money-weighted line of the portfolio and of each benchmark's
    shadow on the chart's own dates, and write it into ``points``.

    Each point is solved over every flow before it, so this is CPU-bound:
    call it off the event loop.
    """
    dates = [point.date for point in points]
    values, flows = _aligned(dates, portfolio_values, money_flows)
    series = MoneyWeightedSeriesRead(values=values, flows=flows)
    portfolio = money_weighted_returns(list(zip(dates, values)), dict(zip(dates, flows)))
    for point, (_, value) in zip(points, portfolio):
        point.portfolio = round(value, 6)
    benchmarks: dict[str, list[tuple[date, float]]] = {}
    windows: dict[str, Optional[float]] = {}
    for key, (shadow_values, shadow_flows) in shadows.items():
        held, moved = _aligned(dates, shadow_values, shadow_flows)
        series.benchmark_values[key] = held
        series.benchmark_flows[key] = moved
        line = money_weighted_returns(list(zip(dates, held)), dict(zip(dates, moved)))
        benchmarks[key] = line
        for point, (_, value) in zip(points, line):
            # A benchmark only has points from its own first date on.
            if key in point.benchmarks:
                point.benchmarks[key] = round(value, 6)
        start = benchmark_starts.get(key)
        first = next((index for index, day in enumerate(dates) if start and day >= start), None)
        windows[key] = (
            range_return(dates, values, flows, first, len(dates) - 1) if first is not None else None
        )
    return SolvedSeries(series, portfolio, benchmarks, windows)
