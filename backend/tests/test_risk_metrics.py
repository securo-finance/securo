"""Unit tests for the pure risk statistics (issue #1669)."""

import math
from datetime import date, timedelta

import pytest

from app.services.risk_metrics import (
    DEFAULT_PERIODS_PER_YEAR,
    RiskMetricsError,
    compute_risk_metrics,
    drawdown_series,
    simple_daily_returns,
)


def test_returns_are_simple_daily_changes():
    assert simple_daily_returns([100.0, 110.0, 99.0, 108.9]) == pytest.approx(
        [0.10, -0.10, 0.10]
    )


def test_drawdown_is_measured_from_the_running_peak():
    assert drawdown_series([100.0, 110.0, 99.0, 108.9]) == pytest.approx(
        [0.0, 0.0, -0.10, -0.01]
    )


def test_known_series_matches_hand_computed_statistics():
    values = [100.0, 110.0, 99.0, 108.9]
    returns = [0.10, -0.10, 0.10]
    daily_mean = sum(returns) / len(returns)
    daily_vol = math.sqrt(
        sum((value - daily_mean) ** 2 for value in returns) / (len(returns) - 1)
    )
    expected_volatility = daily_vol * math.sqrt(DEFAULT_PERIODS_PER_YEAR)
    expected_annualized = (108.9 / 100.0) ** (DEFAULT_PERIODS_PER_YEAR / 3) - 1
    expected_downside = math.sqrt(
        sum(min(0.0, value) ** 2 for value in returns) / len(returns)
    )

    metrics = compute_risk_metrics(values)

    assert metrics.observations == 3
    assert metrics.annualized_return == pytest.approx(expected_annualized)
    assert metrics.annualized_volatility == pytest.approx(expected_volatility)
    assert metrics.max_drawdown == pytest.approx(-0.10)
    assert metrics.current_drawdown == pytest.approx(-0.01)
    assert metrics.sharpe == pytest.approx(daily_mean / daily_vol * math.sqrt(252))
    assert metrics.sortino == pytest.approx(
        daily_mean / expected_downside * math.sqrt(DEFAULT_PERIODS_PER_YEAR)
    )
    assert metrics.calmar == pytest.approx(expected_annualized / 0.10)


def test_risk_free_rate_shifts_sharpe_but_not_volatility():
    values = [100.0, 101.0, 100.5, 102.0, 101.5, 103.0]
    without_rf = compute_risk_metrics(values)
    with_rf = compute_risk_metrics(values, risk_free_rate=0.10)

    assert with_rf.annualized_volatility == pytest.approx(without_rf.annualized_volatility)
    assert with_rf.sharpe is not None and without_rf.sharpe is not None
    assert with_rf.sharpe < without_rf.sharpe


def test_sortino_uses_the_minimum_acceptable_return():
    values = [100.0, 101.0, 100.5, 101.5, 101.0, 102.0]
    stricter = compute_risk_metrics(values, minimum_acceptable_return=0.02)
    looser = compute_risk_metrics(values, minimum_acceptable_return=0.0)

    assert stricter.sortino is not None and looser.sortino is not None
    assert stricter.sortino < looser.sortino


def test_zero_volatility_leaves_the_ratios_undefined():
    metrics = compute_risk_metrics([100.0, 100.0, 100.0, 100.0])

    assert metrics.annualized_volatility == pytest.approx(0.0)
    assert metrics.max_drawdown == 0.0
    assert metrics.sharpe is None
    assert metrics.sortino is None
    assert metrics.calmar is None


def test_a_window_without_losses_has_no_sortino():
    metrics = compute_risk_metrics([100.0, 101.0, 103.0, 104.0])

    assert metrics.sortino is None
    assert metrics.max_drawdown == 0.0
    assert metrics.calmar is None


def test_two_values_report_a_return_but_no_ratios():
    metrics = compute_risk_metrics([100.0, 110.0])

    assert metrics.observations == 1
    assert metrics.annualized_return == pytest.approx(1.10**252 - 1)
    assert metrics.annualized_volatility is None
    assert metrics.sharpe is None
    assert metrics.sortino is None
    assert metrics.calmar is None


def test_periods_per_year_is_configurable_for_calendar_series():
    values = [100.0, 101.0, 100.5, 102.0]
    trading = compute_risk_metrics(values)
    calendar = compute_risk_metrics(values, periods_per_year=365)

    assert trading.annualized_volatility is not None
    assert calendar.annualized_volatility is not None
    assert calendar.annualized_volatility > trading.annualized_volatility


def test_a_real_performance_window_is_accepted():
    start = date(2026, 1, 2)
    values = [1.0]
    for _ in range(252):
        values.append(values[-1] * 1.0002)
        start += timedelta(days=1)

    metrics = compute_risk_metrics(values)

    assert metrics.observations == 252
    assert metrics.annualized_return is not None
    assert metrics.annualized_volatility == pytest.approx(0.0)


@pytest.mark.parametrize(
    "values",
    [
        [],
        [100.0],
        [100.0, 0.0],
        [100.0, -5.0],
        [100.0, float("nan")],
        [100.0, float("inf")],
        [100.0, "not-a-number"],
    ],
)
def test_invalid_series_are_rejected(values):
    with pytest.raises(RiskMetricsError):
        compute_risk_metrics(values)


@pytest.mark.parametrize("periods", [0, -1, 2.5, True])
def test_invalid_periods_per_year_are_rejected(periods):
    with pytest.raises(RiskMetricsError):
        compute_risk_metrics([100.0, 101.0], periods_per_year=periods)


@pytest.mark.parametrize("rate", [-1.0, float("nan"), float("inf")])
def test_invalid_rates_are_rejected(rate):
    with pytest.raises(RiskMetricsError):
        compute_risk_metrics([100.0, 101.0], risk_free_rate=rate)
