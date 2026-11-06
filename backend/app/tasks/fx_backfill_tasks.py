import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.app_clock import use_timezone
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

    Calculates rows with no primary amount, and adds currency provenance to
    existing primary amounts without changing their amount or FX rate. Existing
    values may use a user-selected rate and must not be silently revalued.
    """
    from app.services.fx_rate_service import sync_rates, convert, _resolve_rate

    engine, session_maker = _make_session_maker()
    try:
        stats = {"transactions": 0, "recurring": 0, "assets": 0, "rates_synced": 0}

        async with session_maker() as session, use_timezone(session, fresh=True):
            # 1. Get all users up front so we can tell cross-currency rows apart.
            users_result = await session.execute(select(User))
            settings = get_settings()
            users = {u.id: u.primary_currency for u in users_result.scalars().all()}

            def _primary(user_id) -> str:
                return users.get(user_id, settings.default_currency)

            # 2. Collect transactions that need a value or a currency stamp.
            tx_result = await session.execute(
                select(Transaction).where(
                    or_(
                        Transaction.amount_primary.is_(None),
                        Transaction.amount_primary_currency.is_(None),
                    )
                )
            )
            candidate_txs = [
                tx for tx in tx_result.scalars().all()
                if (
                    tx.amount_primary is None
                    or tx.currency != _primary(tx.user_id)
                    or tx.amount_primary_currency is None
                )
            ]

            # Existing amounts only need provenance. Sync rates only for rows
            # whose primary amount is actually missing.
            months = {
                tx.date.strftime("%Y-%m")
                for tx in candidate_txs
                if tx.amount_primary is None
            }
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

            # 4. Stamp provenance without recalculation, or calculate missing
            # amounts only when a real rate is resolvable.
            for tx in candidate_txs:
                primary_currency = _primary(tx.user_id)
                if tx.amount_primary is not None:
                    if tx.amount_primary_currency is None:
                        tx.amount_primary_currency = primary_currency
                        stats["transactions"] += 1
                    continue
                try:
                    rate = await _resolve_rate(
                        session, tx.currency, primary_currency, tx.date,
                    )
                    if rate is None:
                        continue  # still no rate — leave as-is, heal next run
                    tx.amount_primary = (Decimal(str(tx.amount)) * rate).quantize(Decimal("0.01"))
                    tx.amount_primary_currency = primary_currency
                    tx.fx_rate_used = rate
                    stats["transactions"] += 1
                except Exception:
                    logger.exception("Failed to backfill tx %s", tx.id)
            await session.commit()

            # 5. Backfill recurring transactions only when their primary amount
            #    is absent; never replace an existing user-selected conversion.
            rec_result = await session.execute(
                select(RecurringTransaction).where(
                    RecurringTransaction.amount_primary.is_(None)
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
