import logging
from datetime import date

from sqlalchemy import select, update

from app.core.database import async_session_maker
from app.models.invoice import Invoice
from app.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.invoice_tasks.check_overdue_invoices")
def check_overdue_invoices() -> str:
    """Find sent invoices past their due date and mark them overdue."""
    import asyncio
    return asyncio.run(_check_overdue_invoices_async())


async def _check_overdue_invoices_async() -> str:
    today = date.today()
    try:
        async with async_session_maker() as session:
            # Find invoices that are 'sent' or 'partial' and past their due date
            stmt = (
                update(Invoice)
                .where(
                    Invoice.status.in_(["sent", "partial"]),
                    Invoice.due_date < today
                )
                .values(status="overdue")
            )
            result = await session.execute(stmt)
            await session.commit()
            
            count = result.rowcount
            msg = f"Marked {count} invoices as overdue."
            logger.info(msg)
            return msg
    except Exception as e:
        logger.exception("Failed to check overdue invoices")
        return f"Error: {e}"
