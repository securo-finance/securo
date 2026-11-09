"""Scheduled-sync backoff for connections whose scheduled syncs keep failing."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.bank_connection import BankConnection
from app.providers.base import ConnectionData, ProviderNotConfiguredError, ProviderRateLimited
from app.services.connection_service import handle_oauth_callback, sync_connection
from app.tasks.sync_tasks import (
    SYNC_BACKOFF_KEY,
    _record_sync_failure,
    _sync_all,
    _sync_backoff_delay,
    _sync_backoff_until,
)

H = timedelta(hours=1)


@pytest.mark.parametrize(
    ("failures", "rate_limited", "base", "expected"),
    [
        (1, False, 4 * H, 4 * H),
        (2, False, 4 * H, 8 * H),
        (3, False, 4 * H, 16 * H),
        (4, False, 4 * H, 24 * H),
        (50, False, 4 * H, 24 * H),
        (10_000, False, 0.5 * H, 24 * H),
        # A 429 waits at least 6h, the delay Enable Banking advises.
        (1, True, 1 * H, 6 * H),
        (1, True, 4 * H, 6 * H),
        (3, True, 4 * H, 16 * H),
        # Never retried sooner than a healthy connection would be synced.
        (1, False, 48 * H, 48 * H),
        (5, False, 48 * H, 48 * H),
    ],
)
def test_backoff_delay(failures, rate_limited, base, expected):
    assert _sync_backoff_delay(failures, rate_limited, base) == expected


@pytest.mark.parametrize(
    "settings",
    [
        None,
        {},
        {SYNC_BACKOFF_KEY: "junk"},
        {SYNC_BACKOFF_KEY: {"failures": 0, "last_attempt_at": "2026-01-01T00:00:00+00:00"}},
        {SYNC_BACKOFF_KEY: {"failures": 2, "last_attempt_at": "not a date"}},
        {SYNC_BACKOFF_KEY: {"failures": 2, "last_attempt_at": "2026-01-01T00:00:00"}},
        {SYNC_BACKOFF_KEY: {"failures": "2", "last_attempt_at": "2026-01-01T00:00:00+00:00"}},
    ],
)
def test_backoff_until_ignores_missing_or_malformed_state(settings):
    assert _sync_backoff_until(settings, 4 * H) is None


def test_backoff_until_counts_from_last_attempt():
    last = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
    state = {"failures": 2, "last_attempt_at": last.isoformat(), "rate_limited": False}
    assert _sync_backoff_until({SYNC_BACKOFF_KEY: state}, 4 * H) == last + 8 * H


def _connection(user_id, workspace_id, *, status="error", backoff=None, last_sync_age=10 * H):
    now = datetime.now(timezone.utc)
    return BankConnection(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id,
        provider="test", external_id=str(uuid.uuid4()), institution_name="Test bank",
        credentials={"synthetic": True}, status=status, created_at=now,
        last_sync_at=now - last_sync_age,
        settings={"payee_source": "auto", **({SYNC_BACKOFF_KEY: backoff} if backoff else {})},
    )


def _backoff(failures, age, *, rate_limited=False):
    return {
        "failures": failures,
        "last_attempt_at": (datetime.now(timezone.utc) - age).isoformat(),
        "rate_limited": rate_limited,
    }


async def _run_sync_all(session, sync_one, hours=4):
    engine = AsyncMock()
    settings = Settings(bank_sync_min_interval_hours=hours, _env_file=None, _secrets_dir=None)
    with patch("app.tasks.sync_tasks._make_session_maker", return_value=(engine, lambda: session)), \
         patch("app.tasks.sync_tasks.get_settings", return_value=settings), \
         patch("app.tasks.sync_tasks.get_timezone", new_callable=AsyncMock,
               return_value=ZoneInfo("UTC")), \
         patch("app.tasks.sync_tasks._sync_one", sync_one):
        return await _sync_all()


@pytest.mark.asyncio
async def test_scheduler_skips_connections_still_backing_off(
    session: AsyncSession, test_user, test_workspace,
):
    cases = {
        "no_backoff": (_connection(test_user.id, test_workspace.id), True),
        # 1 failure, base 4h: retry allowed 4h after the failed attempt.
        "first_failure_recent": (
            _connection(test_user.id, test_workspace.id, backoff=_backoff(1, 2 * H)), False,
        ),
        "first_failure_elapsed": (
            _connection(test_user.id, test_workspace.id, backoff=_backoff(1, 5 * H)), True,
        ),
        # 3 failures: 16h.
        "third_failure_recent": (
            _connection(test_user.id, test_workspace.id, backoff=_backoff(3, 10 * H)), False,
        ),
        # Capped at 24h however many failures there were.
        "many_failures_capped": (
            _connection(test_user.id, test_workspace.id, backoff=_backoff(30, 25 * H)), True,
        ),
        # A 429 waits at least 6h even on the first failure.
        "rate_limited_recent": (
            _connection(
                test_user.id, test_workspace.id, status="active",
                backoff=_backoff(1, 5 * H, rate_limited=True),
            ),
            False,
        ),
        "rate_limited_elapsed": (
            _connection(
                test_user.id, test_workspace.id, status="active",
                backoff=_backoff(1, 7 * H, rate_limited=True),
            ),
            True,
        ),
    }
    session.add_all(conn for conn, _ in cases.values())
    await session.commit()

    sync_one = AsyncMock(return_value="refreshed")
    synced = await _run_sync_all(session, sync_one)

    expected = {conn.id for conn, eligible in cases.values() if eligible}
    assert {call.args[1] for call in sync_one.await_args_list} == expected
    assert synced == len(expected)


@pytest.mark.asyncio
async def test_scheduler_records_consecutive_failures(
    session: AsyncSession, test_user, test_workspace,
):
    conn = _connection(test_user.id, test_workspace.id)
    session.add(conn)
    await session.commit()

    failing = AsyncMock(side_effect=RuntimeError("ASPSP_ERROR"))
    assert await _run_sync_all(session, failing) == 0
    conn = await _reload(session, conn.id)
    state = conn.settings[SYNC_BACKOFF_KEY]
    assert state["failures"] == 1
    assert state["rate_limited"] is False
    # Unrelated settings survive the bookkeeping.
    assert conn.settings["payee_source"] == "auto"

    # The next hourly run leaves it alone: the 4h backoff has not elapsed.
    assert await _run_sync_all(session, failing) == 0
    assert failing.await_count == 1

    # Once it has, the retry runs; a second failure doubles the wait.
    conn = await _reload(session, conn.id)
    conn.settings = {**conn.settings, SYNC_BACKOFF_KEY: _backoff(1, 5 * H)}
    await session.commit()
    assert await _run_sync_all(session, failing) == 0
    assert failing.await_count == 2
    conn = await _reload(session, conn.id)
    assert conn.settings[SYNC_BACKOFF_KEY]["failures"] == 2
    retry_at = _sync_backoff_until(conn.settings, 4 * H)
    assert retry_at is not None
    assert retry_at - datetime.now(timezone.utc) > 7 * H


@pytest.mark.asyncio
async def test_scheduler_records_rate_limited_outcome(
    session: AsyncSession, test_user, test_workspace,
):
    conn = _connection(test_user.id, test_workspace.id, status="active")
    session.add(conn)
    await session.commit()

    assert await _run_sync_all(session, AsyncMock(return_value="rate_limited")) == 0
    conn = await _reload(session, conn.id)
    state = conn.settings[SYNC_BACKOFF_KEY]
    assert state == {**state, "failures": 1, "rate_limited": True}
    retry_at = _sync_backoff_until(conn.settings, 4 * H)
    assert retry_at is not None
    assert retry_at - datetime.now(timezone.utc) > 5 * H


@pytest.mark.asyncio
async def test_missing_provider_config_does_not_count_as_failure(
    session: AsyncSession, test_user, test_workspace,
):
    """A worker missing the provider's env is a deployment problem, not the
    connection's: it must not push the connection into backoff."""
    conn = _connection(test_user.id, test_workspace.id)
    session.add(conn)
    await session.commit()

    await _run_sync_all(session, AsyncMock(side_effect=ProviderNotConfiguredError("x")))
    conn = await _reload(session, conn.id)
    assert SYNC_BACKOFF_KEY not in conn.settings


async def _reload(session, conn_id):
    # _sync_all closes the session it was handed, detaching loaded rows.
    conn = await session.get(BankConnection, conn_id, populate_existing=True)
    assert conn is not None
    return conn


def _provider(**overrides):
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "fake"})
    provider.get_accounts = AsyncMock(return_value=[])
    for name, value in overrides.items():
        setattr(provider, name, value)
    return provider


def _service_patches(provider):
    return (
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock),
        patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock),
    )


@pytest.mark.asyncio
async def test_successful_sync_clears_backoff(session: AsyncSession, test_user, test_workspace):
    conn = _connection(test_user.id, test_workspace.id, backoff=_backoff(3, 1 * H))
    session.add(conn)
    await session.commit()

    p1, p2, p3, p4 = _service_patches(_provider())
    with p1, p2, p3, p4:
        result, _, outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert outcome == "refreshed"
    assert result.settings is not None
    assert SYNC_BACKOFF_KEY not in result.settings
    assert result.settings["payee_source"] == "auto"


@pytest.mark.asyncio
async def test_rate_limited_sync_keeps_backoff(session: AsyncSession, test_user, test_workspace):
    backoff = _backoff(2, 1 * H, rate_limited=True)
    conn = _connection(test_user.id, test_workspace.id, status="active", backoff=backoff)
    session.add(conn)
    await session.commit()

    provider = _provider(get_accounts=AsyncMock(side_effect=ProviderRateLimited("429")))
    p1, p2, p3, p4 = _service_patches(provider)
    with p1, p2, p3, p4:
        result, _, outcome = await sync_connection(
            session, conn.id, test_workspace.id, test_user.id
        )

    assert outcome == "rate_limited"
    assert result.settings is not None
    assert result.settings[SYNC_BACKOFF_KEY] == backoff


@pytest.mark.asyncio
async def test_manual_sync_bypasses_backoff(
    client: AsyncClient, auth_headers, session: AsyncSession, test_connection: BankConnection,
):
    """The user asked for this sync, so it runs even while the scheduler waits,
    and its success ends the backoff."""
    test_connection.status = "error"
    test_connection.settings = {SYNC_BACKOFF_KEY: _backoff(2, 1 * H)}
    await session.commit()

    provider = _provider()
    p1, p2, p3, p4 = _service_patches(provider)
    with p1, p2, p3, p4:
        resp = await client.post(
            f"/api/connections/{test_connection.id}/sync", headers=auth_headers,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["sync_outcome"] == "refreshed"
    assert body["status"] == "active"
    assert SYNC_BACKOFF_KEY not in (body["settings"] or {})
    provider.get_accounts.assert_awaited_once()


@pytest.mark.asyncio
async def test_failure_superseded_by_a_later_success_is_not_recorded(
    session: AsyncSession, test_user, test_workspace,
):
    """A manual sync that succeeded while the scheduled one was failing wins:
    the stale failure must neither restore backoff nor drop its settings."""
    attempt_started_at = datetime.now(timezone.utc)
    conn = _connection(test_user.id, test_workspace.id, status="active", last_sync_age=0 * H)
    conn.last_sync_at = attempt_started_at + timedelta(seconds=5)
    conn.settings = {"payee_source": "auto", "last_provider_refresh_at": "2026-11-07T10:00:00+00:00"}
    session.add(conn)
    await session.commit()
    conn_id = conn.id

    await _record_sync_failure(
        lambda: session, conn_id, rate_limited=False,
        attempt_started_at=attempt_started_at, now=datetime.now(timezone.utc),
    )

    conn = await _reload(session, conn_id)
    assert conn.settings == {
        "payee_source": "auto", "last_provider_refresh_at": "2026-11-07T10:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_failure_after_an_older_success_is_recorded(
    session: AsyncSession, test_user, test_workspace,
):
    conn = _connection(test_user.id, test_workspace.id, last_sync_age=10 * H)
    session.add(conn)
    await session.commit()

    await _record_sync_failure(
        lambda: session, conn.id, rate_limited=False,
        attempt_started_at=datetime.now(timezone.utc), now=datetime.now(timezone.utc),
    )

    conn = await _reload(session, conn.id)
    assert conn.settings[SYNC_BACKOFF_KEY]["failures"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("rate_limited", "kept"), [(False, False), (True, True)])
async def test_reconnect_ends_a_credential_backoff_only(
    session: AsyncSession, test_user, test_workspace, rate_limited, kept,
):
    """New credentials make the connection eligible on the next cycle; the
    bank's request quota does not reset with them."""
    backoff = _backoff(3, 1 * H, rate_limited=rate_limited)
    conn = _connection(test_user.id, test_workspace.id, backoff=backoff)
    session.add(conn)
    await session.commit()

    provider = AsyncMock()
    provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id="new-session", institution_name="Test bank",
        credentials={"fresh": True}, accounts=[],
    ))
    with patch("app.services.connection_service.get_provider", return_value=provider):
        result = await handle_oauth_callback(
            session, test_workspace.id, test_user.id, "code",
            provider_name="test", reconnect_connection_id=conn.id,
        )

    assert result.settings is not None
    assert result.settings["payee_source"] == "auto"
    assert (SYNC_BACKOFF_KEY in result.settings) is kept
    retry_at = _sync_backoff_until(result.settings, 4 * H)
    assert (retry_at is not None and retry_at > datetime.now(timezone.utc)) is kept
