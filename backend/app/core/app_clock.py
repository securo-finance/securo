"""Application calendar dates; persisted timestamps continue to use UTC."""

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import date, datetime
import logging
from os import getenv
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import tzlocal

_timezone: ContextVar[ZoneInfo | None] = ContextVar("app_timezone", default=None)
logger = logging.getLogger(__name__)


def environment_timezone() -> ZoneInfo:
    # Preserve the process environment as an explicit file-based override.
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
    from app.models.app_settings import AppSetting

    name = await session.scalar(select(AppSetting.value).where(AppSetting.key == "timezone"))
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            # Keep recovery routes usable after a restore or tzdata change.
            logger.warning("Saved application timezone is unavailable; using environment timezone")
    return environment_timezone()


def app_timezone() -> ZoneInfo:
    """Return the timezone captured for this operation, or the process fallback."""
    return _timezone.get() or environment_timezone()


def app_today() -> date:
    return datetime.now(app_timezone()).date()


@contextmanager
def use_resolved_timezone(timezone: ZoneInfo):
    """Carry an already resolved timezone across an operation boundary."""
    token = _timezone.set(timezone)
    try:
        yield
    finally:
        _timezone.reset(token)


@asynccontextmanager
async def use_timezone(session: AsyncSession):
    with use_resolved_timezone(await get_timezone(session)):
        yield
