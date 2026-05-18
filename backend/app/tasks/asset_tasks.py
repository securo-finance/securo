import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.worker import celery_app
from app.core.config import get_settings
from app.models.asset import Asset
from app.models.asset_value import AssetValue
from app.services.asset_service import refresh_all_market_prices

logger = logging.getLogger(__name__)


def _make_session_maker():
    """Create a fresh engine+session for the Celery worker event loop."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _next_due_date(last_date: date, frequency: str) -> date:
    """Calculate the next due date based on frequency."""
    if frequency == "daily":
        return last_date + timedelta(days=1)
    elif frequency == "weekly":
        return last_date + timedelta(weeks=1)
    elif frequency == "monthly":
        month = last_date.month + 1
        year = last_date.year
        if month > 12:
            month = 1
            year += 1
        day = min(last_date.day, 28)  # safe for all months
        return date(year, month, day)
    elif frequency == "yearly":
        return date(last_date.year + 1, last_date.month, last_date.day)
    return last_date + timedelta(days=1)


async def _apply_growth_rules() -> int:
    """Apply growth rules for all assets that have valuation_method='growth_rule'."""
    engine, session_maker = _make_session_maker()
    try:
        today = date.today()
        total = 0

        async with session_maker() as session:
            result = await session.execute(
                select(Asset).where(
                    Asset.valuation_method == "growth_rule",
                    Asset.growth_type.isnot(None),
                    Asset.growth_rate.isnot(None),
                    Asset.growth_frequency.isnot(None),
                    Asset.is_archived == False,
                    Asset.sell_date.is_(None),
                )
            )
            assets = list(result.scalars().all())

        for asset in assets:
            try:
                async with session_maker() as session:
                    # Get latest value
                    val_result = await session.execute(
                        select(AssetValue)
                        .where(AssetValue.asset_id == asset.id)
                        .order_by(AssetValue.date.desc(), AssetValue.id.desc())
                        .limit(1)
                    )
                    latest = val_result.scalar_one_or_none()
                    if not latest:
                        continue

                    # Check if growth should start
                    if asset.growth_start_date and today < asset.growth_start_date:
                        continue

                    # Generate all missed periods in a loop
                    current_date = latest.date
                    current_amount = float(latest.amount)
                    created = 0

                    while True:
                        next_due = _next_due_date(current_date, asset.growth_frequency)
                        if next_due > today:
                            break

                        if asset.growth_type == "percentage":
                            current_amount = current_amount * (1 + float(asset.growth_rate) / 100)
                        elif asset.growth_type == "absolute":
                            current_amount = current_amount + float(asset.growth_rate)
                        else:
                            break

                        new_value = AssetValue(
                            asset_id=asset.id,
                            amount=Decimal(str(round(current_amount, 6))),
                            date=next_due,
                            source="rule",
                        )
                        session.add(new_value)
                        current_date = next_due
                        created += 1

                        # Safety limit to avoid infinite loops
                        if created >= 1000:
                            break

                    if created > 0:
                        await session.commit()
                        total += created
                        logger.info(
                            "Growth rule applied for asset %s: %d values created, latest=%.2f",
                            asset.id, created, current_amount,
                        )
            except Exception:
                logger.exception("Failed to apply growth rule for asset %s", asset.id)

    finally:
        await engine.dispose()
    return total


@celery_app.task(name="app.tasks.asset_tasks.apply_asset_growth_rules")
def apply_asset_growth_rules() -> dict:
    """Celery task: apply growth rules for all assets."""
    total = asyncio.run(_apply_growth_rules())
    logger.info("Asset growth rules complete: %d values created", total)
    return {"created": total}


async def _refresh_market_prices() -> dict[str, int]:
    """Refresh live prices for every market-priced asset across all users.

    Uses a fresh async session per task run (same pattern as the growth-rule
    task above) to avoid sharing engines with the FastAPI request lifecycle.
    """
    engine, session_maker = _make_session_maker()
    try:
        async with session_maker() as session:
            return await refresh_all_market_prices(session)
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.asset_tasks.refresh_market_prices")
def refresh_market_prices() -> dict:
    """Celery task: re-quote every market-priced asset via the configured provider.

    Scheduled frequently enough that the cached price stays fresh for users,
    but respectful of Yahoo's unofficial rate limits — the service halts
    itself on ``MarketPriceRateLimitedError`` so we don't hammer the API.
    """
    try:
        result = asyncio.run(_refresh_market_prices())
    except Exception:
        logger.exception("Market-price refresh failed")
        return {"error": True, "refreshed": 0, "skipped": 0, "rate_limited": 0}

    logger.info(
        "Market-price refresh complete: %d refreshed, %d skipped, %d rate-limited",
        result.get("refreshed", 0),
        result.get("skipped", 0),
        result.get("rate_limited", 0),
    )
    return result
