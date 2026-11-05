import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.app_clock import (
    get_timezone,
    get_workspace_timezone,
    use_resolved_timezone,
    use_timezone,
)
from app.worker import celery_app
from app.core.config import get_settings
from app.models.bank_connection import BankConnection
from app.providers.base import ProviderNotConfiguredError
from app.services import connection_service
from app.services.connection_service import SyncOutcome

logger = logging.getLogger(__name__)

PROVIDER_REFRESH_THRESHOLD = timedelta(hours=20)


def _should_trigger_provider_refresh(settings: dict | None, now: datetime) -> bool:
    value = (settings or {}).get("last_provider_refresh_at")
    if not isinstance(value, str):
        return True
    try:
        last_refresh = datetime.fromisoformat(value)
    except ValueError:
        return True
    if last_refresh.tzinfo is None:
        return True
    return now - last_refresh >= PROVIDER_REFRESH_THRESHOLD


def _make_session_maker():
    """Create a fresh engine+session for the Celery worker event loop."""
    engine = create_async_engine(get_settings().database_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _sync_all() -> dict:
    """Find stale connections and sync each one."""
    engine, session_maker = _make_session_maker()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=get_settings().bank_sync_min_interval_hours
        )
        synced = 0
        rate_limited = 0
        backing_off = 0

        async with session_maker() as session:
            operation_timezone = await get_timezone(session, fresh=True)
            result = await session.execute(
                select(
                    BankConnection.id,
                    BankConnection.user_id,
                    BankConnection.last_sync_at,
                    BankConnection.settings,
                ).where(
                    BankConnection.status.in_(["active", "error"]),
                    (BankConnection.last_sync_at < cutoff)
                    | (BankConnection.last_sync_at.is_(None)),
                )
            )
            connections = result.all()

        logger.info(
            "Sync check: found %d stale connections (cutoff=%s)",
            len(connections),
            cutoff.isoformat(),
        )

        with use_resolved_timezone(operation_timezone):
            for conn_id, user_id, last_sync, settings in connections:
                cooldown_until = connection_service.rate_limited_until(settings)
                if cooldown_until is not None and cooldown_until > datetime.now(
                    timezone.utc
                ):
                    # The provider throttled this connection recently. Leave it
                    # alone until the cooldown ends: every attempt before then
                    # spends the next day's quota and fails again.
                    logger.info(
                        "Skipping connection %s: rate-limited until %s",
                        conn_id,
                        cooldown_until.isoformat(),
                    )
                    backing_off += 1
                    continue
                try:
                    logger.info("Syncing connection %s (last_sync=%s)", conn_id, last_sync)
                    outcome = await _sync_one(
                        session_maker,
                        conn_id,
                        user_id,
                        trigger_provider_refresh=_should_trigger_provider_refresh(
                            settings, datetime.now(timezone.utc)
                        ),
                    )
                    if outcome not in ("partial", "rate_limited"):
                        synced += 1
                    else:
                        # The provider throttled this run (fully or for some
                        # accounts). The connection is healthy and retries
                        # after its cooldown, so don't count it as synced:
                        # some of its accounts were never fetched.
                        rate_limited += 1
                        logger.warning(
                            "Scheduled sync: connection %s rate-limited by its "
                            "provider (outcome=%s)",
                            conn_id,
                            outcome,
                        )
                except ProviderNotConfiguredError as exc:
                    # Actionable one-liner instead of a buried traceback: this
                    # means THIS process is missing the provider's configuration.
                    logger.error("Skipping connection %s: %s", conn_id, exc)
                except Exception:
                    logger.exception("Background sync failed for connection %s", conn_id)

        return {
            "synced": synced,
            "rate_limited": rate_limited,
            "backing_off": backing_off,
        }
    finally:
        await engine.dispose()


async def _sync_one(
    session_maker,
    connection_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    trigger_provider_refresh: bool = False,
) -> SyncOutcome | None:
    """Sync a single connection and return its outcome (None when skipped).

    Error status is set by sync_connection itself."""
    async with session_maker() as session:
        workspace_id = await session.scalar(
            select(BankConnection.workspace_id).where(BankConnection.id == connection_id)
        )
        if workspace_id is None:
            logger.warning("Connection %s has no workspace; skipping sync", connection_id)
            return None
        with use_resolved_timezone(await get_workspace_timezone(session, workspace_id)):
            _, _, outcome = await connection_service.sync_connection(
                session,
                connection_id,
                workspace_id,
                user_id,
                trigger_provider_refresh=trigger_provider_refresh,
            )
            return outcome


@celery_app.task(name="app.tasks.sync_tasks.sync_all_connections")
def sync_all_connections() -> dict:
    """Celery task: sync all stale bank connections."""
    result = asyncio.run(_sync_all())
    logger.info(
        "Background sync complete: %d connections synced, %d rate-limited, "
        "%d backing off",
        result["synced"],
        result["rate_limited"],
        result["backing_off"],
    )
    return result


@celery_app.task(name="app.tasks.sync_tasks.sync_single_connection")
def sync_single_connection(connection_id: str, user_id: str) -> dict:
    """Celery task: sync a single connection (used for on-demand dispatch)."""
    try:
        asyncio.run(_sync_one_celery(connection_id, user_id))
        return {"status": "ok", "connection_id": connection_id}
    except Exception as e:
        logger.exception("Sync task failed for connection %s", connection_id)
        return {"status": "error", "connection_id": connection_id, "error": str(e)}


async def _sync_one_celery(connection_id: str, user_id: str) -> None:
    engine, session_maker = _make_session_maker()
    try:
        async with session_maker() as session:
            conn_uuid = uuid.UUID(connection_id)
            workspace_id = await session.scalar(
                select(BankConnection.workspace_id).where(BankConnection.id == conn_uuid)
            )
            if workspace_id is None:
                logger.warning("Connection %s has no workspace; skipping sync", connection_id)
                return
            async with use_timezone(session, workspace_id, fresh=True):
                await connection_service.sync_connection(
                    session, conn_uuid, workspace_id, uuid.UUID(user_id)
                )
    finally:
        await engine.dispose()
