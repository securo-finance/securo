import asyncio
import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.worker import celery_app
from app.core.config import get_settings
from app.services import debt_installment_service

logger = logging.getLogger(__name__)


def _make_session_maker():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _settle_payroll_installments() -> int:
    engine, session_maker = _make_session_maker()
    try:
        async with session_maker() as session:
            return await debt_installment_service.auto_settle_payroll_installments(session, date.today())
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.debt_tasks.settle_payroll_installments")
def settle_payroll_installments() -> dict:
    """Mark payroll-deducted debt installments paid once due — the payroll
    deduction itself already happened before the salary was deposited, so
    there is no transaction to wait for or match against."""
    total = asyncio.run(_settle_payroll_installments())
    logger.info("Payroll debt installment settlement: %d installments marked paid", total)
    return {"settled": total}
