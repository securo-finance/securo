"""Unit tests for period_service — pure date math, no DB."""
from datetime import date

import pytest

from app.services.period_service import PeriodRange, resolve_period, shift_anchor


# -------------------------------------------------------------------- resolve


def test_resolve_daily_returns_same_day():
    r = resolve_period("daily", date(2026, 6, 15))
    assert r == PeriodRange(date(2026, 6, 15), date(2026, 6, 15))


def test_resolve_weekly_sunday_start():
    # Anchor Sun Jun 14 2026 → Sun..Sat = Jun 14..20
    r = resolve_period("weekly", date(2026, 6, 14), week_start=0)
    assert r == PeriodRange(date(2026, 6, 14), date(2026, 6, 20))
    # Anchor Wed Jun 17 2026 → still Jun 14..20
    r = resolve_period("weekly", date(2026, 6, 17), week_start=0)
    assert r == PeriodRange(date(2026, 6, 14), date(2026, 6, 20))
    # Anchor Sat Jun 20 2026 → Jun 14..20
    r = resolve_period("weekly", date(2026, 6, 20), week_start=0)
    assert r == PeriodRange(date(2026, 6, 14), date(2026, 6, 20))


def test_resolve_weekly_monday_start():
    # Anchor Mon Jun 15 2026 → Mon..Sun = Jun 15..21
    r = resolve_period("weekly", date(2026, 6, 15), week_start=1)
    assert r == PeriodRange(date(2026, 6, 15), date(2026, 6, 21))
    # Anchor Sun Jun 14 2026 → previous Mon-week = Jun 8..14
    r = resolve_period("weekly", date(2026, 6, 14), week_start=1)
    assert r == PeriodRange(date(2026, 6, 8), date(2026, 6, 14))
    # Anchor Wed Jun 17 2026 → Jun 15..21
    r = resolve_period("weekly", date(2026, 6, 17), week_start=1)
    assert r == PeriodRange(date(2026, 6, 15), date(2026, 6, 21))


def test_resolve_weekly_crosses_month_boundary():
    # Anchor Wed May 27 2026, Sunday-start → May 24..30
    r = resolve_period("weekly", date(2026, 5, 27), week_start=0)
    assert r == PeriodRange(date(2026, 5, 24), date(2026, 5, 30))
    # Anchor Fri May 29 2026, Monday-start → May 25..31
    r = resolve_period("weekly", date(2026, 5, 29), week_start=1)
    assert r == PeriodRange(date(2026, 5, 25), date(2026, 5, 31))


def test_resolve_weekly_crosses_year_boundary():
    # Anchor Sat Jan 2 2027, Sunday-start → Dec 27 2026..Jan 2 2027
    r = resolve_period("weekly", date(2027, 1, 2), week_start=0)
    assert r == PeriodRange(date(2026, 12, 27), date(2027, 1, 2))


@pytest.mark.parametrize(
    "anchor,expected",
    [
        (date(2026, 6, 15), PeriodRange(date(2026, 6, 1), date(2026, 6, 30))),
        (date(2026, 4, 1), PeriodRange(date(2026, 4, 1), date(2026, 4, 30))),
        (date(2026, 2, 10), PeriodRange(date(2026, 2, 1), date(2026, 2, 28))),  # non-leap
        (date(2024, 2, 10), PeriodRange(date(2024, 2, 1), date(2024, 2, 29))),  # leap
        (date(2026, 12, 31), PeriodRange(date(2026, 12, 1), date(2026, 12, 31))),
        (date(2026, 1, 1), PeriodRange(date(2026, 1, 1), date(2026, 1, 31))),
    ],
)
def test_resolve_monthly(anchor, expected):
    assert resolve_period("monthly", anchor) == expected


@pytest.mark.parametrize(
    "anchor,expected_start_month,expected_end_month",
    [
        (date(2026, 2, 15), 1, 3),   # Q1
        (date(2026, 5, 15), 4, 6),   # Q2
        (date(2026, 8, 15), 7, 9),   # Q3
        (date(2026, 11, 15), 10, 12),  # Q4
        (date(2026, 1, 1), 1, 3),    # year edge
        (date(2026, 3, 31), 1, 3),   # last day of Q1
    ],
)
def test_resolve_quarterly(anchor, expected_start_month, expected_end_month):
    r = resolve_period("quarterly", anchor)
    assert r.start == date(anchor.year, expected_start_month, 1)
    expected_end_day = {
        3: 31, 6: 30, 9: 30, 12: 31,
    }[expected_end_month]
    assert r.end == date(anchor.year, expected_end_month, expected_end_day)


@pytest.mark.parametrize(
    "anchor,expected_start_month",
    [
        (date(2026, 1, 15), 1),   # S1
        (date(2026, 3, 31), 1),
        (date(2026, 6, 30), 1),
        (date(2026, 7, 1), 7),    # S2
        (date(2026, 9, 15), 7),
        (date(2026, 12, 31), 7),
    ],
)
def test_resolve_half_yearly(anchor, expected_start_month):
    r = resolve_period("half_yearly", anchor)
    assert r.start == date(anchor.year, expected_start_month, 1)
    expected_end_month = expected_start_month + 5
    expected_end_day = {6: 30, 12: 31}[expected_end_month]
    assert r.end == date(anchor.year, expected_end_month, expected_end_day)


def test_resolve_yearly():
    r = resolve_period("yearly", date(2026, 6, 15))
    assert r == PeriodRange(date(2026, 1, 1), date(2026, 12, 31))


def test_resolve_custom_raises():
    with pytest.raises(ValueError, match="custom mode requires explicit from/to"):
        resolve_period("custom", date(2026, 6, 15))


def test_resolve_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown period mode"):
        resolve_period("bogus", date(2026, 6, 15))  # type: ignore[arg-type]


# --------------------------------------------------------------------- shift


def test_shift_daily():
    assert shift_anchor("daily", date(2026, 6, 15), direction=1) == date(2026, 6, 16)
    assert shift_anchor("daily", date(2026, 6, 15), direction=-1) == date(2026, 6, 14)
    # Crosses month
    assert shift_anchor("daily", date(2026, 6, 30), direction=1) == date(2026, 7, 1)
    # Crosses year
    assert shift_anchor("daily", date(2026, 12, 31), direction=1) == date(2027, 1, 1)


def test_shift_weekly():
    assert shift_anchor("weekly", date(2026, 6, 15), direction=1) == date(2026, 6, 22)
    assert shift_anchor("weekly", date(2026, 6, 15), direction=-1) == date(2026, 6, 8)


def test_shift_monthly_handles_day_overflow():
    # Jan 31 → Feb 28 (non-leap)
    assert shift_anchor("monthly", date(2026, 1, 31), direction=1) == date(2026, 2, 28)
    # Jan 31 → Feb 29 (leap)
    assert shift_anchor("monthly", date(2024, 1, 31), direction=1) == date(2024, 2, 29)
    # Mar 31 → Feb 28 (back direction, non-leap)
    assert shift_anchor("monthly", date(2026, 3, 31), direction=-1) == date(2026, 2, 28)
    # Crosses year forward
    assert shift_anchor("monthly", date(2026, 12, 15), direction=1) == date(2027, 1, 15)
    # Crosses year backward
    assert shift_anchor("monthly", date(2026, 1, 15), direction=-1) == date(2025, 12, 15)


def test_shift_quarterly():
    assert shift_anchor("quarterly", date(2026, 2, 15), direction=1) == date(2026, 5, 15)
    assert shift_anchor("quarterly", date(2026, 5, 15), direction=-1) == date(2026, 2, 15)
    # Cross year
    assert shift_anchor("quarterly", date(2026, 11, 15), direction=1) == date(2027, 2, 15)


def test_shift_half_yearly():
    assert shift_anchor("half_yearly", date(2026, 3, 15), direction=1) == date(2026, 9, 15)
    assert shift_anchor("half_yearly", date(2026, 9, 15), direction=-1) == date(2026, 3, 15)


def test_shift_yearly():
    assert shift_anchor("yearly", date(2024, 6, 15), direction=1) == date(2025, 6, 15)
    # Feb 29 leap → Feb 28 non-leap when stepping forward
    assert shift_anchor("yearly", date(2024, 2, 29), direction=1) == date(2025, 2, 28)
    assert shift_anchor("yearly", date(2026, 6, 15), direction=-1) == date(2025, 6, 15)


def test_shift_custom_raises():
    with pytest.raises(ValueError, match="custom mode"):
        shift_anchor("custom", date(2026, 6, 15), direction=1)


def test_shift_invalid_direction_raises():
    with pytest.raises(ValueError, match="direction must be -1 or 1"):
        shift_anchor("daily", date(2026, 6, 15), direction=5)


# --------------------------------------------------------- compose round-trip


def test_shift_then_resolve_yields_neighbor_period():
    """For monthly/quarterly/half_yearly/yearly, shifting the anchor by +1 and
    resolving yields the next-period's range. This guards against off-by-one
    drift in shift_anchor vs resolve_period."""
    a = date(2026, 6, 15)
    next_a = shift_anchor("monthly", a, direction=1)
    assert resolve_period("monthly", a).as_tuple() == (date(2026, 6, 1), date(2026, 6, 30))
    assert resolve_period("monthly", next_a).as_tuple() == (date(2026, 7, 1), date(2026, 7, 31))

    next_q = shift_anchor("quarterly", date(2026, 2, 15), direction=1)
    assert resolve_period("quarterly", next_q).as_tuple() == (date(2026, 4, 1), date(2026, 6, 30))

    next_y = shift_anchor("yearly", date(2026, 6, 15), direction=1)
    assert resolve_period("yearly", next_y).as_tuple() == (date(2027, 1, 1), date(2027, 12, 31))