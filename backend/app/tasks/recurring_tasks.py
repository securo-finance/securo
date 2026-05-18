import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.worker import celery_app
from app.core.config import get_settings
from app.models.user import User
from app.services import recurring_transaction_service

logger = logging.getLogger(__name__)


def _make_session_maker():
    """Create a fresh engine+session for the Celery worker event loop."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _generate_all() -> int:
    """Generate pending recurring transactions for all users."""
    engine, session_maker = _make_session_maker()
    try:
        total = 0

        async with session_maker() as session:
            result = await session.execute(select(User.id))
            user_ids = [row[0] for row in result.all()]

        for user_id in user_ids:
            try:
                async with session_maker() as session:
                    count = await recurring_transaction_service.generate_pending(session, user_id)
                    if count:
                        logger.info("Generated %d recurring transactions for user %s", count, user_id)
                        total += count
            except Exception:
                logger.exception("Failed to generate recurring transactions for user %s", user_id)

    finally:
        await engine.dispose()
    return total


@celery_app.task(name="app.tasks.recurring_tasks.generate_all_recurring")
def generate_all_recurring() -> dict:
    """Celery task: generate pending recurring transactions for all users."""
    total = asyncio.run(_generate_all())
    logger.info("Recurring generation complete: %d transactions created", total)
    return {"generated": total}
