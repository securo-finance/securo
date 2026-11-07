import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.app_clock import use_timezone
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
        async with session_maker() as session, use_timezone(session, fresh=True):
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
        async with session_maker() as session, use_timezone(session, fresh=True):
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


async def _restamp_fallback_transactions() -> int:
    """Heal cross-currency transactions that lack a real FX rate.

    Targets rows left NULL or missing the currency provenance added in migration
    103. Existing primary amounts are only stamped with their currency; they are
    never recalculated, since their rate may have been set by the user. Rows with
    no primary amount are calculated only when a real rate is available. Uses only already-stored rates
    (``allow_fetch=False``) so a large heal never hammers the FX provider — the
    daily ``sync_fx_rates`` task keeps rates fresh.

    Existing values and user-set rates are preserved. Missing conversions remain
    NULL until a real stored rate is available.
    """
    from sqlalchemy import or_, select

    from app.models.transaction import Transaction
    from app.models.user import User
    from app.services.fx_rate_service import stamp_primary_amount

    settings = get_settings()
    engine, session_maker = _make_session_maker()
    try:
        async with session_maker() as session, use_timezone(session, fresh=True):
            users = {
                u.id: u.primary_currency
                for u in (await session.execute(select(User))).scalars().all()
            }
            result = await session.execute(
                select(Transaction).where(
                    or_(
                        Transaction.amount_primary.is_(None),
                        Transaction.amount_primary_currency.is_(None),
                    )
                )
            )
            count = 0
            for tx in result.scalars().all():
                primary = users.get(tx.user_id, settings.default_currency)
                if tx.amount_primary is not None:
                    # A legacy value may reflect a user-selected/custom FX rate.
                    # Backfill provenance only; do not replace that amount/rate.
                    if tx.amount_primary_currency is None:
                        tx.amount_primary_currency = primary
                        count += 1
                    continue
                before = (tx.amount_primary, tx.fx_rate_used, tx.amount_primary_currency)
                await stamp_primary_amount(session, tx.user_id, tx, allow_fetch=False)
                if (tx.amount_primary, tx.fx_rate_used, tx.amount_primary_currency) != before:
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


@celery_app.task(name="app.tasks.fx_rate_tasks.restamp_fallback_fx")
def restamp_fallback_fx() -> dict:
    """Celery task: heal cross-currency transactions stamped with the 1:1 fallback
    (or left NULL) once real rates are available. Skips if fx_sync_mode != scheduled."""
    settings = get_settings()
    if settings.fx_sync_mode != "scheduled":
        logger.debug("Fallback FX heal skipped (fx_sync_mode=%s)", settings.fx_sync_mode)
        return {"skipped": True, "reason": "fx_sync_mode is not scheduled"}
    count = asyncio.run(_restamp_fallback_transactions())
    logger.info("Fallback FX heal complete: %d transactions healed", count)
    return {"restamped": count}
