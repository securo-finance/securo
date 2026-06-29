"""Period resolution: turn a (mode, anchor_date) pair into a [from, to] range.

The frontend period selector lets users browse by Diário/Semanal/Mensal/Trimestral/
Semestral/Anual/Personalizado without picking two dates. This service is the single
source of truth for the [from, to] calculation so backend endpoints stay trivial and
the frontend twin (`frontend/src/lib/period.ts`) can mirror it exactly.

Civil calendar conventions (locked 2026-06-29):
- Quarterly: Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec
- Half-yearly: S1=Jan-Jun, S2=Jul-Dec
- Weekly: week_start drives the boundary. Default 0=Sunday (PT-BR); EN locales pass 1.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal


PeriodMode = Literal["daily", "weekly", "monthly", "quarterly", "half_yearly", "yearly", "custom"]


@dataclass(frozen=True)
class PeriodRange:
    """A inclusive [start, end] date range."""

    start: date
    end: date

    def as_tuple(self) -> tuple[date, date]:
        return (self.start, self.end)


def _last_day_of_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def _start_of_week(d: date, week_start: int) -> date:
    """Return the start of the week containing `d`. week_start: 0=Sun, 1=Mon, ..., 6=Sat."""
    # Python's weekday(): Mon=0..Sun=6. Convert to Sunday=0..Saturday=6.
    py_weekday = d.weekday()
    sunday_based = (py_weekday + 1) % 7
    offset = (sunday_based - week_start) % 7
    return d - timedelta(days=offset)


def resolve_period(
    mode: PeriodMode,
    anchor: date,
    *,
    week_start: int = 0,
) -> PeriodRange:
    """Compute the [start, end] range for `mode` rooted at `anchor`.

    Args:
        mode: one of the 7 period modes. `custom` is rejected — callers must supply
            from/to directly for that mode.
        anchor: a date inside the desired period. For boundary cases (e.g. anchor
            is the last day of a 31-day month, target month has 30 days) the period
            is the month of the anchor's actual month, not a shifted month.
        week_start: 0=Sunday, 1=Monday, ..., 6=Saturday. Only used for `weekly`.

    Raises:
        ValueError: on unknown mode or `custom` (custom uses from/to directly).
    """
    if mode == "custom":
        raise ValueError("custom mode requires explicit from/to; resolve_period does not handle it")
    if mode not in ("daily", "weekly", "monthly", "quarterly", "half_yearly", "yearly"):
        raise ValueError(f"unknown period mode: {mode!r}")

    if mode == "daily":
        return PeriodRange(anchor, anchor)

    if mode == "weekly":
        start = _start_of_week(anchor, week_start)
        end = start + timedelta(days=6)
        return PeriodRange(start, end)

    if mode == "monthly":
        y, m = anchor.year, anchor.month
        start = date(y, m, 1)
        end = date(y, m, _last_day_of_month(y, m))
        return PeriodRange(start, end)

    if mode == "quarterly":
        quarter = (anchor.month - 1) // 3  # 0..3
        first_month = quarter * 3 + 1
        start = date(anchor.year, first_month, 1)
        last_month = first_month + 2
        end = date(anchor.year, last_month, _last_day_of_month(anchor.year, last_month))
        return PeriodRange(start, end)

    if mode == "half_yearly":
        semester = 0 if anchor.month <= 6 else 1  # 0=S1 (Jan-Jun), 1=S2 (Jul-Dec)
        first_month = 1 if semester == 0 else 7
        start = date(anchor.year, first_month, 1)
        last_month = first_month + 5
        end = date(anchor.year, last_month, _last_day_of_month(anchor.year, last_month))
        return PeriodRange(start, end)

    # yearly
    return PeriodRange(date(anchor.year, 1, 1), date(anchor.year, 12, 31))


def shift_anchor(mode: PeriodMode, anchor: date, *, direction: int, week_start: int = 0) -> date:
    """Step the anchor by one `mode` worth, in `direction` (+1 / -1).

    Used by the frontend arrow buttons (◀ / ▶) to compute the next anchor without
    round-tripping through from/to. Mode `custom` is rejected — the frontend
    disables arrows for that mode anyway.
    """
    if mode == "custom":
        raise ValueError("cannot shift anchor for custom mode")
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")

    if mode == "daily":
        return anchor + timedelta(days=direction)

    if mode == "weekly":
        return anchor + timedelta(days=7 * direction)

    if mode == "monthly":
        # Compute target via 0-based month index so Jan +1 wraps to Feb in the
        # same year and Dec +1 wraps to Jan of next year (and Jan -1 wraps to
        # Dec of prev year). Clamp day to handle Jan 31 + 1 → Feb 28/29.
        m_index = (anchor.month - 1) + direction
        target_y = anchor.year + m_index // 12
        target_m = m_index % 12 + 1
        target_d = min(anchor.day, _last_day_of_month(target_y, target_m))
        return date(target_y, target_m, target_d)

    if mode == "quarterly":
        # Step by 3 months.
        m_index = (anchor.month - 1) + 3 * direction
        target_y = anchor.year + m_index // 12
        target_m = m_index % 12 + 1
        target_d = min(anchor.day, _last_day_of_month(target_y, target_m))
        return date(target_y, target_m, target_d)

    if mode == "half_yearly":
        m_index = (anchor.month - 1) + 6 * direction
        target_y = anchor.year + m_index // 12
        target_m = m_index % 12 + 1
        target_d = min(anchor.day, _last_day_of_month(target_y, target_m))
        return date(target_y, target_m, target_d)

    # yearly
    return date(anchor.year + direction, anchor.month, min(anchor.day, _last_day_of_month(anchor.year + direction, anchor.month)))