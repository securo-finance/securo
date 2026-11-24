"""Scheduled syncs of plugin connections.

Celery never runs plugin code: these tasks only ask the plugin runner to call
a connection's `sync`, or one of its routines (ADR 0035), and record the outcome. While PLUGINS_ENABLED is off or
no runner is configured they return without touching the database.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.feature_flags import feature_flag
from app.core.redis import close_redis
from app.models.plugin_connection import PluginConnection, PluginConnectionRoutine
from app.plugins import connections, runner
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return feature_flag("PLUGINS_ENABLED") and runner.configured()


def _session_maker():
    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def sync_due(session: AsyncSession) -> int:
    """Sync every scheduled connection whose time has come; returns how many ran."""
    rows = await session.scalars(
        select(PluginConnection).where(
            PluginConnection.sync_minutes.is_not(None), PluginConnection.status != "needs_auth"
        )
    )
    ran = 0
    due = [c.id for c in rows.all() if connections.due(c)]
    for connection_id in due:
        # One connection failing (or removed meanwhile) never stops the others.
        try:
            connection = await session.get(PluginConnection, connection_id, populate_existing=True)
            if connection is None or not connections.due(connection):
                continue
            await connections.sync(session, connection)
            ran += 1
        except Exception:
            logger.exception("Plugin connection %s failed to sync", connection_id)
            await session.rollback()
    return ran


async def run_due_routines(session: AsyncSession) -> int:
    """Run every routine whose time has come; returns how many ran."""
    rows = await session.execute(
        select(PluginConnectionRoutine.id, PluginConnectionRoutine.connection_id)
        .join(PluginConnection, PluginConnection.id == PluginConnectionRoutine.connection_id)
        .where(PluginConnection.status != "needs_auth")
    )
    ran = 0
    for routine_id, connection_id in rows.all():
        # One routine failing (or removed meanwhile) never stops the others.
        try:
            routine = await session.get(PluginConnectionRoutine, routine_id, populate_existing=True)
            connection = await session.get(PluginConnection, connection_id, populate_existing=True)
            if routine is None or connection is None or not connections.routine_due(routine, connection):
                continue
            if await connections.run_routine(session, connection, routine, scheduled=True) == "ran":
                ran += 1
        except Exception:
            logger.exception("Plugin routine %s failed to run", routine_id)
            await session.rollback()
    return ran


async def _sync_due() -> int:
    engine, session_maker = _session_maker()
    try:
        async with session_maker() as session:
            return await sync_due(session) + await run_due_routines(session)
    finally:
        await engine.dispose()
        # Each task runs in its own event loop: don't leave a client bound to this one.
        await close_redis()


async def _sync_one(connection_id: str) -> None:
    engine, session_maker = _session_maker()
    try:
        async with session_maker() as session:
            connection = await session.get(PluginConnection, uuid.UUID(connection_id))
            if connection is not None and connection.status != "needs_auth":
                await connections.sync(session, connection)
    finally:
        await engine.dispose()
        await close_redis()


@celery_app.task(name="app.tasks.plugin_tasks.sync_plugin_connections")
def sync_plugin_connections() -> dict:
    if not _enabled():
        return {"synced": 0}
    synced = asyncio.run(_sync_due())
    if synced:
        logger.info("Plugin connections synced: %d", synced)
    return {"synced": synced}


async def _run_routines_of(
    connection_id: str, *, force: bool = False, only: list[str] | None = None
) -> list[str]:
    """Run the connection's due routines, or with `force` every one (`only` narrows it).

    Returns the forced routines that found the connection busy and didn't run:
    they still have to prove the new values, so the caller tries them again.
    An unforced routine that finds it busy is left to the schedule, which runs
    it once the lock is free because it is still due.
    """
    engine, session_maker = _session_maker()
    busy: list[str] = []
    try:
        async with session_maker() as session:
            connection = await session.get(PluginConnection, uuid.UUID(connection_id))
            if connection is None:
                return []
            for routine in await connections.list_routines(session, [connection.id]):
                if only is not None and routine.routine not in only:
                    continue
                # Forced: the values just changed and this run is what proves
                # them, so a routine not yet due runs anyway.
                if not force and not connections.routine_due(routine, connection):
                    continue
                outcome = await connections.run_routine(session, connection, routine, scheduled=not force)
                if outcome == "needs_auth":
                    return []  # the whole connection needs signing in again: nothing left to retry
                if outcome == "busy" and force:
                    busy.append(routine.routine)
    finally:
        await engine.dispose()
        await close_redis()
    return busy


#: A forced round that finds the connection busy tries those routines again this
#: often, this many times (about ten minutes), before leaving them to their schedule.
FORCED_RETRY_SECONDS = 30
FORCED_RETRIES = 20


@celery_app.task(name="app.tasks.plugin_tasks.run_plugin_routines")
def run_plugin_routines(connection_id: str, force: bool = False, only: list[str] | None = None, attempt: int = 0) -> dict:
    """A connection's routines right after it was set up, or every one of them after
    new values (ADR 0038); the schedule does the rest."""
    if not _enabled():
        return {"status": "disabled"}
    busy = asyncio.run(_run_routines_of(connection_id, force=force, only=only))
    if not busy:
        return {"status": "ok"}
    if attempt >= FORCED_RETRIES:
        logger.warning("Plugin connection %s stayed busy; routines %s wait for their schedule", connection_id, busy)
        return {"status": "busy", "routines": busy}
    run_plugin_routines.apply_async((connection_id, True, busy, attempt + 1), countdown=FORCED_RETRY_SECONDS)
    return {"status": "retrying", "routines": busy}


@celery_app.task(name="app.tasks.plugin_tasks.sync_plugin_connection")
def sync_plugin_connection(connection_id: str) -> dict:
    if not _enabled():
        return {"status": "disabled"}
    asyncio.run(_sync_one(connection_id))
    return {"status": "ok"}
