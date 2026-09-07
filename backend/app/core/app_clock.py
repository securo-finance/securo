"""Application calendar dates; persisted timestamps continue to use UTC."""

from contextlib import asynccontextmanager
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


def app_today() -> date:
    return datetime.now(_timezone.get() or environment_timezone()).date()


@asynccontextmanager
async def use_timezone(session: AsyncSession):
    token = _timezone.set(await get_timezone(session))
    try:
        yield
    finally:
        _timezone.reset(token)
