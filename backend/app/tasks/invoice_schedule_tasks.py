"""Emit the invoices of recurring agreements whose period has come.

One schedule per transaction, so a failure on one agreement never
takes the others down with it, and the failure count that eventually
pauses a broken schedule is committed even when the emission is not.
"""
import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.workspace import Workspace
from app.services import invoice_schedule_service, invoice_service
from app.services.invoice_service import InvoiceError
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _make_session_maker():
    """A fresh engine and session factory for the worker's event loop."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _generate_one(session_maker, schedule_id) -> int:
    """Emit what one agreement is due. Returns how many invoices it made."""
    async with session_maker() as session:
        schedule = await session.get(invoice_schedule_service.InvoiceSchedule, schedule_id)
        if schedule is None:
            return 0
        try:
            emitted = await invoice_schedule_service.generate_due(session, schedule)
        except Exception as exc:
            if isinstance(exc, InvoiceError) and exc.code == "period_already_issued":
                # Another run emitted the period first. Nothing failed,
                # and `generate_due` already rolled back.
                return 0
            # `generate_due` already counted the failure on the row and
            # may have paused the schedule; what it did not do is commit.
            # Anything half-written for the invoice is rolled back, then
            # the count is written on its own.
            logger.exception("Failed to emit invoices for schedule %s", schedule_id)
            failures = schedule.consecutive_failures
            status = schedule.status
            pause_reason = schedule.pause_reason
            await session.rollback()
            fresh = await session.get(invoice_schedule_service.InvoiceSchedule, schedule_id)
            if fresh is not None:
                fresh.consecutive_failures = failures
                fresh.status = status
                fresh.pause_reason = pause_reason
                await session.commit()
            return 0
        await session.commit()

        workspace = await session.get(Workspace, schedule.workspace_id)
        for invoice in emitted:
            loaded = await invoice_service.get_invoice(session, invoice.id, schedule.workspace_id)
            if loaded is not None and workspace is not None:
                await invoice_schedule_service.after_generation(session, loaded, workspace)
        return len(emitted)


async def _generate_all() -> int:
    engine, session_maker = _make_session_maker()
    try:
        async with session_maker() as session:
            schedule_ids = await invoice_schedule_service.due_schedule_ids(session)
        total = 0
        for schedule_id in schedule_ids:
            total += await _generate_one(session_maker, schedule_id)
    finally:
        await engine.dispose()
    return total


@celery_app.task(name="app.tasks.invoice_schedule_tasks.generate_recurring_invoices")
def generate_recurring_invoices() -> dict:
    """Celery task: emit every recurring invoice whose period has come."""
    total = asyncio.run(_generate_all())
    if total:
        logger.info("Recurring invoices: %d emitted", total)
    return {"generated": total}
