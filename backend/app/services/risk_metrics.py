"""Risk statistics for a daily, flow-adjusted portfolio series.

The Performance tab (#1096) already builds a daily time-weighted return index
per scope and a daily price series per benchmark. This module turns either
series into the numbers a user compares: how much the value moved
(volatility), how deep it fell (drawdown), and whether the return paid for
that risk (Sharpe, Sortino, Calmar).

Conventions:

* values are daily index levels (``1.0`` at the start of the window) or any
  positive daily series such as an adjusted price; returns are simple, not
  log returns, so a time-weighted return index maps directly.
* there are 252 periods per year by default, because Yahoo, the B3 index and
  the portfolio series are trading-day series. A calendar-daily series passes
  ``365``.
* the risk-free rate is annual and converted with
  ``(1 + rf) ** (1 / periods_per_year) - 1``. The caller decides where it
  comes from; zero is the default and the Índice DI is the natural CDI proxy
  once the performance tab exposes it.
* ratios with an undefined denominator are ``None``, never zero. A Sharpe of
  zero means "no excess return"; it does not mean "no volatility to divide
  by". The same holds for Sortino without downside days and Calmar without a
  drawdown.
* drawdowns are negative fractions from the running peak: a 10% fall is
  ``-0.10``.
* annualized return and Calmar need at least five daily returns. Annualizing
  one good day (a 10% day becomes 2.7e10 per year) says nothing about a
  portfolio.

The module is deliberately free of database, HTTP and clock access so the
math is unit-tested on its own and reused by the API, the MCP server and any
later report.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, stdev
from typing import Optional, Sequence

DEFAULT_PERIODS_PER_YEAR = 252

#: Annualizing a window shorter than a trading week turns one good day into a
#: meaningless figure (a 10% day becomes 2.7e10 per year), so the annualized
#: return and Calmar stay undefined until there are five daily returns.
MIN_ANNUALIZATION_OBSERVATIONS = 5


class RiskMetricsError(ValueError):
    """A series that cannot produce risk statistics was passed in."""


@dataclass(frozen=True)
class RiskMetrics:
    """Risk statistics for one window.

    ``observations`` counts the daily returns, so a series of ``n`` values
    reports ``n - 1`` observations. ``None`` marks a statistic the window
    cannot define, such as volatility from a single return.
    """

    observations: int
    annualized_return: Optional[float]
    annualized_volatility: Optional[float]
    max_drawdown: Optional[float]
    current_drawdown: Optional[float]
    sharpe: Optional[float]
    sortino: Optional[float]
    calmar: Optional[float]


def _validated(values: Sequence[float], periods_per_year: int) -> list[float]:
    if isinstance(periods_per_year, bool) or not isinstance(periods_per_year, int):
        raise RiskMetricsError("periods_per_year must be an integer")
    if periods_per_year < 1:
        raise RiskMetricsError("periods_per_year must be at least 1")
    cleaned: list[float] = []
    for position, value in enumerate(values):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RiskMetricsError(f"value at position {position} is not a number") from exc
        if not math.isfinite(number):
            raise RiskMetricsError(f"value at position {position} is not finite")
        if number <= 0:
            raise RiskMetricsError(
                f"value at position {position} must be greater than zero"
            )
        cleaned.append(number)
    if len(cleaned) < 2:
        raise RiskMetricsError("at least two values are needed for any statistic")
    return cleaned


def simple_daily_returns(values: Sequence[float]) -> list[float]:
    """Daily simple returns between consecutive positive values."""

    cleaned = _validated(values, DEFAULT_PERIODS_PER_YEAR)
    return [cleaned[i] / cleaned[i - 1] - 1 for i in range(1, len(cleaned))]


def drawdown_series(values: Sequence[float]) -> list[float]:
    """Drawdown of each value from the running peak, as a negative fraction."""

    cleaned = _validated(values, DEFAULT_PERIODS_PER_YEAR)
    peak = cleaned[0]
    drawdowns: list[float] = []
    for value in cleaned:
        peak = max(peak, value)
        drawdowns.append(value / peak - 1)
    return drawdowns


def _annualized_return(cleaned: list[float], periods_per_year: int) -> Optional[float]:
    periods = len(cleaned) - 1
    try:
        # A window shorter than a year is raised to a fraction of a year, so a
        # three-month gain reads as the yearly rate it would imply.
        return (cleaned[-1] / cleaned[0]) ** (periods_per_year / periods) - 1
    except (OverflowError, ZeroDivisionError):
        return None


def compute_risk_metrics(
    values: Sequence[float],
    *,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
    risk_free_rate: float = 0.0,
    minimum_acceptable_return: float = 0.0,
) -> RiskMetrics:
    """Risk statistics for a daily series of positive values.

    ``values`` are index levels (or adjusted prices) ordered oldest first. The
    first value anchors the window: its own return is not part of the series.

    Raises :class:`RiskMetricsError` for a non-positive, non-finite or too
    short series, and for rates that cannot be converted to a daily figure.
    """

    cleaned = _validated(values, periods_per_year)
    for name, rate in (
        ("risk_free_rate", risk_free_rate),
        ("minimum_acceptable_return", minimum_acceptable_return),
    ):
        try:
            number = float(rate)
        except (TypeError, ValueError) as exc:
            raise RiskMetricsError(f"{name} is not a number") from exc
        if not math.isfinite(number):
            raise RiskMetricsError(f"{name} is not finite")
        if number <= -1:
            raise RiskMetricsError(f"{name} must be greater than -1")
        if name == "risk_free_rate":
            risk_free_rate = number
        else:
            minimum_acceptable_return = number

    returns = [cleaned[i] / cleaned[i - 1] - 1 for i in range(1, len(cleaned))]
    drawdowns = drawdown_series(cleaned)
    scale = math.sqrt(periods_per_year)
    annualized_return = (
        _annualized_return(cleaned, periods_per_year)
        if len(returns) >= MIN_ANNUALIZATION_OBSERVATIONS
        else None
    )
    max_drawdown = min(drawdowns)
    current_drawdown = drawdowns[-1]

    volatility: Optional[float] = None
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    calmar: Optional[float] = None

    if len(returns) >= 2:
        daily_volatility = stdev(returns)
        volatility = daily_volatility * scale
        daily_risk_free = (1 + risk_free_rate) ** (1 / periods_per_year) - 1
        excess_mean = fmean(returns) - daily_risk_free
        if daily_volatility > 0:
            sharpe = excess_mean / daily_volatility * scale

        daily_mar = (1 + minimum_acceptable_return) ** (1 / periods_per_year) - 1
        downside = math.sqrt(
            fmean([min(0.0, value - daily_mar) ** 2 for value in returns])
        )
        if downside > 0:
            sortino = (fmean(returns) - daily_mar) / downside * scale

    if max_drawdown < 0 and annualized_return is not None:
        calmar = annualized_return / abs(max_drawdown)

    return RiskMetrics(
        observations=len(returns),
        annualized_return=annualized_return,
        annualized_volatility=volatility,
        max_drawdown=max_drawdown,
        current_drawdown=current_drawdown,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
    )
