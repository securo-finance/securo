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

logger = logging.getLogger(__name__)

PROVIDER_REFRESH_THRESHOLD = timedelta(hours=20)

# Scheduled-sync backoff for connections whose last scheduled sync failed.
# A failed sync does not advance last_sync_at, so without this the hourly
# beat retries an erroring connection every hour, forever, spending the
# bank's unattended-access quota (commonly ~4 calls a day under PSD2) on
# calls that keep failing. The state lives in BankConnection.settings under
# SYNC_BACKOFF_KEY; any successful sync, manual or scheduled, clears it.
SYNC_BACKOFF_KEY = connection_service.SYNC_BACKOFF_KEY
SYNC_BACKOFF_MAX = timedelta(hours=24)
# Enable Banking advises resuming no earlier than 6 hours after a 429.
SYNC_BACKOFF_RATE_LIMITED_MIN = timedelta(hours=6)


def _sync_backoff_delay(failures: int, rate_limited: bool, base: timedelta) -> timedelta:
    """Wait after `failures` consecutive failed scheduled syncs.

    Doubles from `base` per failure up to SYNC_BACKOFF_MAX, never less than
    `base` itself (a failing connection is not retried sooner than a healthy
    one), and never less than SYNC_BACKOFF_RATE_LIMITED_MIN after a 429.
    """
    # The exponent is capped so the multiplication cannot overflow timedelta;
    # 2**16 times any valid base is already far past the 24h ceiling.
    doubled = base * (2 ** min(max(failures - 1, 0), 16))
    delay = max(base, min(doubled, SYNC_BACKOFF_MAX))
    if rate_limited:
        delay = max(delay, SYNC_BACKOFF_RATE_LIMITED_MIN)
    return delay


def _sync_backoff_until(settings: dict | None, base: timedelta) -> datetime | None:
    """When the scheduler may next try this connection, or None if not backing off."""
    state = (settings or {}).get(SYNC_BACKOFF_KEY)
    if not isinstance(state, dict):
        return None
    failures = state.get("failures")
    last_attempt = state.get("last_attempt_at")
    if not isinstance(failures, int) or failures <= 0 or not isinstance(last_attempt, str):
        return None
    try:
        last_attempt_at = datetime.fromisoformat(last_attempt)
    except ValueError:
        return None
    if last_attempt_at.tzinfo is None:
        return None
    return last_attempt_at + _sync_backoff_delay(
        failures, bool(state.get("rate_limited")), base
    )


async def _record_sync_failure(
    session_maker,
    connection_id: uuid.UUID,
    *,
    rate_limited: bool,
    attempt_started_at: datetime,
    now: datetime,
) -> None:
    """Count one more consecutive failed scheduled sync for the connection.

    The row is locked like sync_connection locks it, so a manual sync running
    meanwhile commits first and its settings are the ones updated here. A sync
    that succeeded after this attempt started supersedes the failure, which is
    then not recorded.
    """
    async with session_maker() as session:
        conn = await session.get(
            BankConnection, connection_id, populate_existing=True, with_for_update=True
        )
        if conn is None:
            return
        last_sync_at = conn.last_sync_at
        if last_sync_at is not None:
            if last_sync_at.tzinfo is None:
                last_sync_at = last_sync_at.replace(tzinfo=timezone.utc)
            if last_sync_at >= attempt_started_at:
                await session.rollback()
                return
        current = dict(conn.settings or {})
        previous = current.get(SYNC_BACKOFF_KEY)
        failures = previous.get("failures") if isinstance(previous, dict) else 0
        if not isinstance(failures, int) or failures < 0:
            failures = 0
        current[SYNC_BACKOFF_KEY] = {
            "failures": failures + 1,
            "last_attempt_at": now.isoformat(),
            "rate_limited": rate_limited,
        }
        conn.settings = current
        await session.commit()


async def _record_sync_failure_safely(
    session_maker,
    connection_id: uuid.UUID,
    *,
    rate_limited: bool,
    attempt_started_at: datetime,
) -> None:
    # Bookkeeping must never stop the loop from reaching the next connection.
    try:
        await _record_sync_failure(
            session_maker,
            connection_id,
            rate_limited=rate_limited,
            attempt_started_at=attempt_started_at,
            now=datetime.now(timezone.utc),
        )
    except Exception:
        logger.exception("Could not record sync backoff for connection %s", connection_id)


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


async def _sync_all() -> int:
    """Find stale connections and sync each one."""
    engine, session_maker = _make_session_maker()
    try:
        min_interval = timedelta(hours=get_settings().bank_sync_min_interval_hours)
        now = datetime.now(timezone.utc)
        cutoff = now - min_interval
        synced = 0

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
            stale = result.all()

        connections = []
        for row in stale:
            retry_at = _sync_backoff_until(row.settings, min_interval)
            if retry_at is not None and retry_at > now:
                logger.info(
                    "Skipping connection %s: backing off after failed syncs until %s",
                    row.id,
                    retry_at.isoformat(),
                )
                continue
            connections.append(row)

        logger.info(
            "Sync check: found %d stale connections, %d backing off (cutoff=%s)",
            len(connections),
            len(stale) - len(connections),
            cutoff.isoformat(),
        )

        with use_resolved_timezone(operation_timezone):
            for conn_id, user_id, last_sync, settings in connections:
                attempt_started_at = datetime.now(timezone.utc)
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
                except ProviderNotConfiguredError as exc:
                    # Actionable one-liner instead of a buried traceback: this
                    # means THIS process is missing the provider's configuration.
                    # Not the connection's fault, so it does not count as a failure.
                    logger.error("Skipping connection %s: %s", conn_id, exc)
                    continue
                except Exception:
                    logger.exception("Background sync failed for connection %s", conn_id)
                    await _record_sync_failure_safely(
                        session_maker,
                        conn_id,
                        rate_limited=False,
                        attempt_started_at=attempt_started_at,
                    )
                    continue
                if outcome == "rate_limited":
                    logger.warning("Background sync for connection %s was rate-limited", conn_id)
                    await _record_sync_failure_safely(
                        session_maker,
                        conn_id,
                        rate_limited=True,
                        attempt_started_at=attempt_started_at,
                    )
                    continue
                synced += 1

        return synced
    finally:
        await engine.dispose()


async def _sync_one(
    session_maker,
    connection_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    trigger_provider_refresh: bool = False,
) -> connection_service.SyncOutcome | None:
    """Sync a single connection and return its outcome (None if skipped).

    Error status is set by sync_connection itself.
    """
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
    synced = asyncio.run(_sync_all())
    logger.info("Background sync complete: %d connections synced", synced)
    return {"synced": synced}


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
