import asyncio
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.worker import celery_app
from app.core.config import get_settings
from app.models.transaction import Transaction
from app.models.recurring_transaction import RecurringTransaction
from app.models.asset import Asset
from app.models.user import User

logger = logging.getLogger(__name__)


def _make_session_maker():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _backfill_primary_amounts() -> dict:
    """One-time backfill: stamp amount_primary on all rows where it's NULL."""
    from app.services.fx_rate_service import sync_rates, convert

    engine, session_maker = _make_session_maker()
    try:
        stats = {"transactions": 0, "recurring": 0, "assets": 0, "rates_synced": 0}

        async with session_maker() as session:
            # 1. Collect all unique (year, month) pairs from transactions needing backfill
            month_result = await session.execute(
                select(distinct(func.to_char(Transaction.date, "YYYY-MM"))).where(
                    Transaction.amount_primary.is_(None)
                )
            )
            months = [row[0] for row in month_result.all()]

            # Sync historical rates for each month
            for month_str in months:
                try:
                    year, mon = month_str.split("-")
                    # Use last day of month for historical rate
                    if int(mon) == 12:
                        target = date(int(year) + 1, 1, 1)
                    else:
                        target = date(int(year), int(mon) + 1, 1)
                    from datetime import timedelta

                    target = target - timedelta(days=1)
                    count = await sync_rates(session, target)
                    stats["rates_synced"] += count
                except Exception:
                    logger.exception("Failed to sync rates for %s", month_str)

            # 2. Get all users
            users_result = await session.execute(select(User))
            settings = get_settings()
            users = {u.id: (u.preferences or {}).get("currency_display", settings.default_currency) for u in users_result.scalars().all()}

            # 3. Backfill transactions
            tx_result = await session.execute(
                select(Transaction).where(Transaction.amount_primary.is_(None))
            )
            for tx in tx_result.scalars().all():
                primary_currency = users.get(tx.user_id, settings.default_currency)
                try:
                    converted, rate = await convert(
                        session, Decimal(str(tx.amount)),
                        tx.currency, primary_currency, tx.date,
                    )
                    tx.amount_primary = converted
                    tx.fx_rate_used = rate
                    stats["transactions"] += 1
                except Exception:
                    logger.exception("Failed to backfill tx %s", tx.id)
            await session.commit()

            # 4. Backfill recurring transactions
            rec_result = await session.execute(
                select(RecurringTransaction).where(RecurringTransaction.amount_primary.is_(None))
            )
            for rec in rec_result.scalars().all():
                primary_currency = users.get(rec.user_id, settings.default_currency)
                try:
                    converted, rate = await convert(
                        session, Decimal(str(rec.amount)),
                        rec.currency, primary_currency, rec.start_date,
                    )
                    rec.amount_primary = converted
                    rec.fx_rate_used = rate
                    stats["recurring"] += 1
                except Exception:
                    logger.exception("Failed to backfill recurring %s", rec.id)
            await session.commit()

            # 5. Backfill assets
            asset_result = await session.execute(
                select(Asset).where(
                    Asset.purchase_price.isnot(None),
                    Asset.purchase_price_primary.is_(None),
                )
            )
            for asset in asset_result.scalars().all():
                primary_currency = users.get(asset.user_id, settings.default_currency)
                try:
                    converted, _ = await convert(
                        session, Decimal(str(asset.purchase_price)),
                        asset.currency, primary_currency, asset.purchase_date,
                    )
                    asset.purchase_price_primary = converted
                    stats["assets"] += 1
                except Exception:
                    logger.exception("Failed to backfill asset %s", asset.id)
            await session.commit()

    finally:
        await engine.dispose()
    return stats


@celery_app.task(name="app.tasks.fx_backfill_tasks.backfill_primary_amounts")
def backfill_primary_amounts() -> dict:
    """Celery task: one-time backfill of amount_primary for all existing records."""
    stats = asyncio.run(_backfill_primary_amounts())
    logger.info("Backfill complete: %s", stats)
    return stats
