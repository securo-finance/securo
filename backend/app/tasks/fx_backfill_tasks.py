import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, or_
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
    """One-time backfill / heal of amount_primary.

    Targets cross-currency rows that were never stamped (``amount_primary`` NULL)
    or that were stamped with the legacy 1:1 fallback rate (issue #353), and
    re-stamps them once a real rate is resolvable. Genuine same-currency 1:1 rows
    are left untouched.
    """
    from app.services.fx_rate_service import sync_rates, convert, _resolve_rate

    engine, session_maker = _make_session_maker()
    try:
        stats = {"transactions": 0, "recurring": 0, "assets": 0, "rates_synced": 0}

        async with session_maker() as session:
            # 1. Get all users up front so we can tell cross-currency rows apart.
            users_result = await session.execute(select(User))
            settings = get_settings()
            users = {u.id: u.primary_currency for u in users_result.scalars().all()}

            def _primary(user_id) -> str:
                return users.get(user_id, settings.default_currency)

            # 2. Collect candidate transactions: never stamped OR stamped with the
            #    1:1 fallback. Same-currency rows are filtered out so we never
            #    re-touch legitimate 1:1 conversions.
            tx_result = await session.execute(
                select(Transaction).where(
                    or_(
                        Transaction.amount_primary.is_(None),
                        Transaction.fx_rate_used == 1,
                    )
                )
            )
            candidate_txs = [
                tx for tx in tx_result.scalars().all()
                if tx.currency != _primary(tx.user_id)
            ]

            # 3. Sync one historical rate per month that has candidates.
            months = {tx.date.strftime("%Y-%m") for tx in candidate_txs}
            for month_str in months:
                try:
                    year, mon = month_str.split("-")
                    # Use last day of month for historical rate
                    if int(mon) == 12:
                        target = date(int(year) + 1, 1, 1)
                    else:
                        target = date(int(year), int(mon) + 1, 1)
                    target = target - timedelta(days=1)
                    stats["rates_synced"] += await sync_rates(session, target)
                except Exception:
                    logger.exception("Failed to sync rates for %s", month_str)

            # 4. Re-stamp candidates only when a real rate is now resolvable.
            for tx in candidate_txs:
                primary_currency = _primary(tx.user_id)
                try:
                    rate = await _resolve_rate(
                        session, tx.currency, primary_currency, tx.date,
                    )
                    if rate is None:
                        continue  # still no rate — leave as-is, heal next run
                    tx.amount_primary = (Decimal(str(tx.amount)) * rate).quantize(Decimal("0.01"))
                    tx.fx_rate_used = rate
                    stats["transactions"] += 1
                except Exception:
                    logger.exception("Failed to backfill tx %s", tx.id)
            await session.commit()

            # 5. Backfill recurring transactions (same NULL-or-1:1 heal).
            rec_result = await session.execute(
                select(RecurringTransaction).where(
                    or_(
                        RecurringTransaction.amount_primary.is_(None),
                        RecurringTransaction.fx_rate_used == 1,
                    )
                )
            )
            candidate_recs = [
                rec for rec in rec_result.scalars().all()
                if rec.currency != _primary(rec.user_id)
            ]
            for rec in candidate_recs:
                primary_currency = _primary(rec.user_id)
                try:
                    rate = await _resolve_rate(
                        session, rec.currency, primary_currency, rec.start_date,
                    )
                    if rate is None:
                        continue
                    rec.amount_primary = (Decimal(str(rec.amount)) * rate).quantize(Decimal("0.01"))
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
