"""Business days, for schedules that follow the bank calendar.

A salary paid on the fifth business day, or a bill that moves off a holiday,
cannot be written as a calendar day: the fifth business day is the 5th one
month and the 9th the next. This module answers the one question those rules
need, *is this a day money moves?*, and the two that follow from it.

A holiday calendar is a country code, optionally with a subdivision after a
hyphen: ``BR``, ``BR-SP``, ``US-NY``, ``PT-30``. The days themselves come from
the ``holidays`` package, which computes them offline for every country it
supports, so a self-hosted install never calls out to a holiday API. Only a
country's public holidays count; the optional and observance categories are
days the banks still open.

Brazil adds its bank calendar on top: FEBRABAN closes the clearing system on
days that are not public holidays in law. A schedule without a calendar
treats every weekday as a business day, which is what it did before this
module.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from functools import lru_cache
from typing import Optional

import holidays


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_offset) // 451
    month, day = divmod(h + weekday_offset - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=64)
def _br_bank_holidays(year: int) -> frozenset[date]:
    """Days Brazilian banks do not clear.

    Carnival Monday and Tuesday and Corpus Christi are not national holidays
    in law, but FEBRABAN closes the clearing system on them, and the clearing
    system is what decides when a salary lands.
    """
    easter = _easter(year)
    days = {
        date(year, 1, 1),    # Confraternização Universal
        date(year, 4, 21),   # Tiradentes
        date(year, 5, 1),    # Dia do Trabalho
        date(year, 9, 7),    # Independência
        date(year, 10, 12),  # Nossa Senhora Aparecida
        date(year, 11, 2),   # Finados
        date(year, 11, 15),  # Proclamação da República
        date(year, 12, 25),  # Natal
        easter - timedelta(days=48),  # Carnaval (segunda)
        easter - timedelta(days=47),  # Carnaval (terça)
        easter - timedelta(days=2),   # Sexta-feira Santa
        easter + timedelta(days=60),  # Corpus Christi
    }
    if year >= 2024:
        days.add(date(year, 11, 20))  # Consciência Negra (Lei 14.759/2023)
    return frozenset(days)


def _split_calendar(holiday_calendar: str) -> tuple[str, Optional[str]]:
    country, _, subdivision = holiday_calendar.partition("-")
    return country, subdivision or None


@lru_cache(maxsize=1)
def supported_holiday_calendars() -> dict[str, dict[str, str]]:
    """Every country the ``holidays`` package covers, with its subdivisions.

    Maps a country code to ``{subdivision code: name}``. The package names
    subdivisions only for some countries; the rest are listed by their code.
    """
    result: dict[str, dict[str, str]] = {}
    for country, subdivisions in holidays.list_supported_countries(include_aliases=False).items():
        names = {
            code: name
            for name, code in getattr(holidays.country_holidays(country), "subdivisions_aliases", {}).items()
        }
        result[country] = {code: names.get(code, code) for code in subdivisions}
    return result


def is_supported_holiday_calendar(holiday_calendar: str) -> bool:
    country, subdivision = _split_calendar(holiday_calendar)
    subdivisions = supported_holiday_calendars().get(country)
    if subdivisions is None:
        return False
    return subdivision is None or subdivision in subdivisions


@lru_cache(maxsize=256)
def _holidays(holiday_calendar: str, year: int) -> frozenset[date]:
    if not is_supported_holiday_calendar(holiday_calendar):
        raise ValueError(f"Unsupported holiday calendar: {holiday_calendar}")
    country, subdivision = _split_calendar(holiday_calendar)
    days = set(holidays.country_holidays(country, subdiv=subdivision, years=year).keys())
    if country == "BR":
        days |= _br_bank_holidays(year)
    return frozenset(days)


def is_holiday(day: date, holiday_calendar: Optional[str]) -> bool:
    if holiday_calendar is None:
        return False
    return day in _holidays(holiday_calendar, day.year)


def is_business_day(
    day: date,
    holiday_calendar: Optional[str] = None,
    count_saturdays: bool = False,
) -> bool:
    """Whether ``day`` counts as a business day.

    ``count_saturdays`` is for the Brazilian labour rule (CLT art. 459), where
    Saturday counts toward the fifth business day of a salary even though no
    money moves on it.
    """
    weekday = day.weekday()
    if weekday == calendar.SUNDAY:
        return False
    if weekday == calendar.SATURDAY and not count_saturdays:
        return False
    return not is_holiday(day, holiday_calendar)


def is_banking_day(day: date, holiday_calendar: Optional[str] = None) -> bool:
    """Whether money can actually move on ``day``: never on a weekend."""
    return is_business_day(day, holiday_calendar, count_saturdays=False)


def previous_banking_day(day: date, holiday_calendar: Optional[str] = None) -> date:
    """``day`` itself when money moves on it, otherwise the banking day before."""
    while not is_banking_day(day, holiday_calendar):
        day -= timedelta(days=1)
    return day


def next_banking_day(day: date, holiday_calendar: Optional[str] = None) -> date:
    """``day`` itself when money moves on it, otherwise the banking day after."""
    while not is_banking_day(day, holiday_calendar):
        day += timedelta(days=1)
    return day


def nth_business_day(
    year: int,
    month: int,
    n: int,
    holiday_calendar: Optional[str] = None,
    count_saturdays: bool = False,
) -> date:
    """The ``n``-th business day of a month, counted from the 1st.

    A month never has fewer than eighteen business days, so an ``n`` past the
    month's last business day clamps to it rather than spilling into the next
    month, the same way day 31 clamps in a short month.
    """
    if n < 1:
        raise ValueError("n must be at least 1")
    last_day = calendar.monthrange(year, month)[1]
    found: Optional[date] = None
    count = 0
    for day_number in range(1, last_day + 1):
        day = date(year, month, day_number)
        if is_business_day(day, holiday_calendar, count_saturdays):
            count += 1
            found = day
            if count == n:
                break
    assert found is not None  # every month has business days
    return found
