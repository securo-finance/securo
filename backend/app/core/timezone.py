from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_APP_TIMEZONE = "UTC"


def is_valid_timezone(value: str) -> bool:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def app_now(timezone_name: str = DEFAULT_APP_TIMEZONE, now: datetime | None = None) -> datetime:
    """Return the current instant in the configured application timezone."""
    tz = ZoneInfo(timezone_name)
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base.astimezone(tz)


def app_today(timezone_name: str = DEFAULT_APP_TIMEZONE, now: datetime | None = None) -> date:
    """Return today's date in the configured application timezone."""
    return app_now(timezone_name, now=now).date()
