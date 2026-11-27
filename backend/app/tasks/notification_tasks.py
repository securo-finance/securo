import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.app_clock import use_timezone
from app.core.config import get_settings
from app.core.row_security import bound_to, workspace_makers
from app.models.workspace import Workspace
from app.services import notification_channels, notification_service
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _make_session_maker():
    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _evaluate_all() -> int:
    engine, session_maker = _make_session_maker()
    try:
        evaluated = 0
        for maker in await workspace_makers(session_maker):
            async with maker() as session:
                query = select(Workspace.id).where(Workspace.is_archived.is_(False))
                # `workspaces` itself has no policy: a bound run takes only its own (ADR 0043).
                if (bound := bound_to(session)) is not None:
                    query = query.where(Workspace.id == bound)
                workspace_ids = (await session.execute(query)).scalars().all()
            for workspace_id in workspace_ids:
                try:
                    async with maker() as session, use_timezone(session, workspace_id, fresh=True):
                        await notification_service.evaluate_workspace(session, workspace_id)
                except Exception:
                    logger.exception("Notification evaluation failed for workspace %s", workspace_id)
            async with maker() as session:
                await notification_service.prune(session)
            evaluated += len(workspace_ids)
        return evaluated
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.notification_tasks.evaluate_notifications")
def evaluate_notifications() -> dict:
    """Celery task: make every workspace's notifications say what is true now."""
    return {"workspaces": asyncio.run(_evaluate_all())}


async def _deliver() -> int:
    engine, session_maker = _make_session_maker()
    try:
        sent = 0
        for maker in await workspace_makers(session_maker):
            async with maker() as session:
                sent += await notification_channels.deliver(session)
        return sent
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.notification_tasks.deliver_notifications")
def deliver_notifications() -> dict:
    """Celery task: send what is due through the channels outside the app."""
    return {"sent": asyncio.run(_deliver())}
