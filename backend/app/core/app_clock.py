"""Calendar dates for the application; persisted timestamps stay in UTC.

Resolution order for "today":

1. The workspace's own timezone, when the operation runs inside a workspace
   that set one.
2. The application timezone saved by an administrator (``app_settings``).
3. The ``TZ`` environment variable.
4. The host timezone.
5. UTC.

Each request, MCP call, CLI run and background job snapshots the timezone
it starts with, so one operation never mixes two calendars if a setting
changes while it runs.
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from functools import lru_cache
from os import getenv
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import tzlocal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
_timezone: ContextVar[ZoneInfo | None] = ContextVar("app_timezone", default=None)

# The saved application timezone is read on every request. It changes a few
# times in the life of an installation, so a short cache keeps that read off
# the database while still letting a new value land within seconds; the admin
# endpoint that writes it drops the cache immediately for its own process.
_CACHE_TTL_SECONDS = 30.0
_saved_cache: tuple[float, Optional[str]] | None = None


# Files that live next to the zones on some hosts without naming one: the
# host's own link, the POSIX rules file and tzdata's placeholder zone.
_NOT_TIMEZONES = frozenset({"localtime", "posixrules", "Factory"})


@lru_cache(maxsize=1)
def timezone_names() -> frozenset[str]:
    """Every IANA key this host can load, for validation and pickers."""
    return frozenset(available_timezones()) - _NOT_TIMEZONES


def is_valid_timezone(name: str) -> bool:
    return name in timezone_names()


def parse_timezone(name: Optional[str]) -> Optional[ZoneInfo]:
    """Load a saved timezone name, or None when it is missing or unknown."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def environment_timezone() -> ZoneInfo:
    """Resolve the deployment timezone, falling back safely to UTC."""
    name = getenv("TZ")
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("TZ environment value is unavailable; using system timezone")

    try:
        return tzlocal.get_localzone()
    except (OSError, ZoneInfoNotFoundError, ValueError):
        logger.warning("System timezone is unavailable; using UTC")
        return ZoneInfo("UTC")


def invalidate_timezone_cache() -> None:
    """Forget the cached application setting; the next read hits the database."""
    global _saved_cache
    _saved_cache = None


async def saved_timezone_name(session: AsyncSession, *, fresh: bool = False) -> Optional[str]:
    """The raw application timezone setting, valid or not, or None when unset.

    ``fresh`` skips the cache. Requests can live with a value up to thirty
    seconds old, but a job that writes dated rows must not: the process that
    saved a new value only dropped its own cache, and a worker that captured
    the old zone could still stamp the old day onto a recurring transaction.
    """
    global _saved_cache
    now = time.monotonic()
    if not fresh and _saved_cache is not None and _saved_cache[0] > now:
        return _saved_cache[1]

    from app.models.app_settings import AppSetting

    name = await session.scalar(select(AppSetting.value).where(AppSetting.key == "timezone"))
    _saved_cache = (now + _CACHE_TTL_SECONDS, name or None)
    return name or None


async def get_timezone(session: AsyncSession, *, fresh: bool = False) -> ZoneInfo:
    """Resolve the application timezone: the saved setting, else the environment."""
    name = await saved_timezone_name(session, fresh=fresh)
    if name:
        timezone = parse_timezone(name)
        if timezone is not None:
            return timezone
        logger.warning("Saved application timezone is unavailable; using environment timezone")
    return environment_timezone()


async def get_workspace_timezone(session: AsyncSession, workspace_id: uuid.UUID) -> ZoneInfo:
    """Resolve the timezone one workspace runs on.

    A workspace with its own timezone uses it; any other workspace follows the
    application timezone captured for the current operation.
    """
    from app.models.workspace import Workspace

    name = await session.scalar(select(Workspace.timezone).where(Workspace.id == workspace_id))
    return workspace_timezone(name)


def workspace_timezone(name: Optional[str]) -> ZoneInfo:
    """Pick between a workspace's saved timezone and the application one."""
    timezone = parse_timezone(name)
    if timezone is not None:
        return timezone
    if name:
        logger.warning("Saved workspace timezone is unavailable; using application timezone")
    return app_timezone()


def app_timezone() -> ZoneInfo:
    """Return the timezone captured for this operation, or the process fallback."""
    return _timezone.get() or environment_timezone()


def app_today() -> date:
    """Return today's calendar date in the timezone of the current operation."""
    return datetime.now(app_timezone()).date()


def today_in(timezone: ZoneInfo) -> date:
    """Return today's calendar date in a specific timezone."""
    return datetime.now(timezone).date()


@contextmanager
def use_resolved_timezone(timezone: ZoneInfo):
    """Carry an already resolved timezone across one operation."""
    token = _timezone.set(timezone)
    try:
        yield
    finally:
        _timezone.reset(token)


@asynccontextmanager
async def use_timezone(
    session: AsyncSession,
    workspace_id: Optional[uuid.UUID] = None,
    *,
    fresh: bool = False,
):
    """Resolve and snapshot the timezone for one database-backed operation.

    With a workspace, the snapshot is that workspace's timezone; without one,
    the application timezone. Jobs pass ``fresh`` to read the saved setting
    rather than a cached copy.
    """
    with use_resolved_timezone(await get_timezone(session, fresh=fresh)):
        if workspace_id is None:
            yield
            return
        with use_resolved_timezone(await get_workspace_timezone(session, workspace_id)):
            yield
