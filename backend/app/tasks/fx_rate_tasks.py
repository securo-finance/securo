import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.worker import celery_app
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _make_session_maker():
    """Create a fresh engine+session for the Celery worker event loop."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _sync_fx_rates() -> int:
    """Fetch latest FX rates and store them."""
    from app.services.fx_rate_service import sync_rates

    engine, session_maker = _make_session_maker()
    try:
        async with session_maker() as session:
            count = await sync_rates(session)
        return count
    finally:
        await engine.dispose()


async def _restamp_recurring_fx() -> int:
    """Re-stamp active recurring transactions with latest FX rates."""
    from sqlalchemy import select

    from app.models.recurring_transaction import RecurringTransaction
    from app.models.user import User
    from app.services.fx_rate_service import stamp_primary_amount

    engine, session_maker = _make_session_maker()
    try:
        async with session_maker() as session:
            users = (await session.execute(select(User))).scalars().all()
        count = 0
        for user in users:
            primary = user.primary_currency
            result = await session.execute(
                select(RecurringTransaction).where(
                    RecurringTransaction.user_id == user.id,
                    RecurringTransaction.is_active == True,
                    RecurringTransaction.currency != primary,
                )
            )
            for rec in result.scalars().all():
                await stamp_primary_amount(
                    session, user.id, rec, date_field="start_date",
                )
                count += 1
        await session.commit()
    finally:
        await engine.dispose()
    return count


@celery_app.task(name="app.tasks.fx_rate_tasks.sync_fx_rates")
def sync_fx_rates() -> dict:
    """Celery task: sync latest FX rates from provider. Skips if fx_sync_mode != scheduled."""
    settings = get_settings()
    if settings.fx_sync_mode != "scheduled":
        logger.debug("FX sync skipped (fx_sync_mode=%s)", settings.fx_sync_mode)
        return {"skipped": True, "reason": "fx_sync_mode is not scheduled"}
    count = asyncio.run(_sync_fx_rates())
    logger.info("FX rate sync complete: %d rates synced", count)
    return {"synced": count}


@celery_app.task(name="app.tasks.fx_rate_tasks.restamp_recurring_fx")
def restamp_recurring_fx() -> dict:
    """Celery task: re-stamp recurring transactions with latest FX rates. Skips if fx_sync_mode != scheduled."""
    settings = get_settings()
    if settings.fx_sync_mode != "scheduled":
        logger.debug("Recurring FX re-stamp skipped (fx_sync_mode=%s)", settings.fx_sync_mode)
        return {"skipped": True, "reason": "fx_sync_mode is not scheduled"}
    count = asyncio.run(_restamp_recurring_fx())
    logger.info("Recurring FX re-stamp complete: %d records updated", count)
    return {"restamped": count}
