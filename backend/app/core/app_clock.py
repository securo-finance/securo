"""Application calendar dates; persisted timestamps continue to use UTC."""

import logging
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from os import getenv
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import tzlocal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
_timezone: ContextVar[ZoneInfo | None] = ContextVar("app_timezone", default=None)


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


async def get_timezone(session: AsyncSession) -> ZoneInfo:
    """Resolve the saved application timezone without caching database state."""
    from app.models.app_settings import AppSetting

    name = await session.scalar(select(AppSetting.value).where(AppSetting.key == "timezone"))
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Saved application timezone is unavailable; using environment timezone")
    return environment_timezone()


def app_timezone() -> ZoneInfo:
    """Return the timezone captured for this operation, or the process fallback."""
    return _timezone.get() or environment_timezone()


def app_today() -> date:
    """Return today's calendar date in the application timezone."""
    return datetime.now(app_timezone()).date()


@contextmanager
def use_resolved_timezone(timezone: ZoneInfo):
    """Carry an already resolved timezone across one operation."""
    token = _timezone.set(timezone)
    try:
        yield
    finally:
        _timezone.reset(token)


@asynccontextmanager
async def use_timezone(session: AsyncSession):
    """Resolve and snapshot the timezone for one database-backed operation."""
    with use_resolved_timezone(await get_timezone(session)):
        yield
