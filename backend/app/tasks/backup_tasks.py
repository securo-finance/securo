import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.services import backup_service
from app.worker import celery_app


def _make_session_maker():
    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _run_scheduled_backups() -> dict:
    engine, session_maker = _make_session_maker()
    try:
        async with session_maker() as session:
            return await backup_service.run_scheduled_backups(session)
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.backup_tasks.run_scheduled_backups")
def run_scheduled_backups() -> dict:
    return asyncio.run(_run_scheduled_backups())
