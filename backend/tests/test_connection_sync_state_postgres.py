"""Connection retry state across real PostgreSQL transactions."""

import asyncio
import importlib.util
from pathlib import Path
import uuid
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.models.bank_connection import BankConnection
from app.providers.base import ConnectionData
from app.services import connection_service

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each PostgreSQL fixture owns a fresh schema."""


async def _connection(session, user, workspace):
    connection = BankConnection(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id,
        provider="test", external_id="sync-test", institution_name="Test Bank",
        credentials={"token": "old"}, settings={"sync_assets": False}, status="active",
    )
    session.add(connection)
    await session.commit()
    return connection


def _provider():
    provider = AsyncMock()
    provider.refresh_credentials.return_value = {"token": "old-refreshed"}
    provider.get_accounts.return_value = []
    provider.handle_oauth_callback.return_value = ConnectionData(
        external_id="reconnected", institution_name="Test Bank",
        credentials={"token": "new"}, accounts=[],
    )
    return provider


async def test_reconnect_waits_for_sync_and_refreshes_cached_state(
    session, postgres_sessions, test_user, test_workspace,
):
    connection = await _connection(session, test_user, test_workspace)
    connection_id, user_id, workspace_id = connection.id, test_user.id, test_workspace.id
    provider = _provider()
    syncing = asyncio.Event()
    release = asyncio.Event()

    async def get_accounts(credentials):
        syncing.set()
        await release.wait()
        return []

    provider.get_accounts.side_effect = get_accounts
    async with postgres_sessions() as sync_session, postgres_sessions() as reconnect_session:
        # A request can have loaded this row before waiting for the sync lock.
        cached = await reconnect_session.get(BankConnection, connection_id)
        assert cached is not None and cached.sync_state_version == 0
        await reconnect_session.commit()
        with patch.object(connection_service, "get_provider", return_value=provider):
            sync_task = asyncio.create_task(connection_service.sync_connection(
                sync_session, connection_id, workspace_id, user_id,
            ))
            await asyncio.wait_for(syncing.wait(), timeout=5)
            reconnect_task = asyncio.create_task(connection_service.handle_oauth_callback(
                reconnect_session, workspace_id, user_id, "setup-token",
                provider_name="test", reconnect_connection_id=connection_id,
            ))
            try:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(reconnect_task), timeout=0.1)
                provider.handle_oauth_callback.assert_not_awaited()
            finally:
                release.set()
                await asyncio.wait_for(sync_task, timeout=5)
                reconnected = await asyncio.wait_for(reconnect_task, timeout=5)

    assert reconnected.credentials == {"token": "new"}
    assert reconnected.last_sync_at is None
    assert reconnected.status == "active"
    # The waiting request must read the committed version before incrementing.
    assert reconnected.sync_state_version == 2


async def test_failed_sync_cannot_overwrite_reconnect_after_rollback(
    session, postgres_sessions, test_user, test_workspace,
):
    connection = await _connection(session, test_user, test_workspace)
    connection_id, user_id, workspace_id = connection.id, test_user.id, test_workspace.id
    provider = _provider()
    provider.get_accounts.side_effect = RuntimeError("temporary provider failure")
    failure_waiting = asyncio.Event()
    release = asyncio.Event()
    update_status = connection_service._set_sync_status_if_current

    async def pause_failure(*args, **kwargs):
        failure_waiting.set()
        await release.wait()
        await update_status(*args, **kwargs)

    async with postgres_sessions() as sync_session, postgres_sessions() as reconnect_session:
        with patch.object(connection_service, "get_provider", return_value=provider), patch.object(
            connection_service, "_set_sync_status_if_current", side_effect=pause_failure,
        ):
            sync_task = asyncio.create_task(connection_service.sync_connection(
                sync_session, connection_id, workspace_id, user_id,
            ))
            await asyncio.wait_for(failure_waiting.wait(), timeout=5)
            try:
                await asyncio.wait_for(connection_service.handle_oauth_callback(
                    reconnect_session, workspace_id, user_id, "setup-token",
                    provider_name="test", reconnect_connection_id=connection_id,
                ), timeout=5)
            finally:
                release.set()
                with pytest.raises(RuntimeError, match="temporary provider failure"):
                    await asyncio.wait_for(sync_task, timeout=5)

    await session.refresh(connection)
    assert connection.credentials == {"token": "new"}
    assert connection.status == "active"
    assert connection.last_sync_at is None
    assert connection.last_sync_error_account_id is None
    assert connection.sync_state_version == 1


async def test_sync_state_migration_defaults_and_round_trip(
    session, test_user, test_workspace, monkeypatch,
):
    connection = await _connection(session, test_user, test_workspace)
    connection_id = connection.id
    path = Path(__file__).resolve().parents[1] / "alembic/versions/103_bank_connection_sync_state.py"
    spec = importlib.util.spec_from_file_location("sync_state_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = session.bind.get_execution_options()["schema_translate_map"][None]
    sql_connection = await session.connection()
    await sql_connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    await sql_connection.execute(text(
        "ALTER TABLE bank_connections DROP COLUMN sync_state_version, "
        "DROP COLUMN last_sync_error_account_id"
    ))

    def run(sync_connection):
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(sync_connection)))
        migration.upgrade()
        row = sync_connection.execute(text(
            "SELECT sync_state_version, last_sync_error_account_id "
            "FROM bank_connections WHERE id=:id"
        ), {"id": connection_id}).one()
        assert row == (0, None)
        migration.downgrade()
        assert sync_connection.execute(text(
            "SELECT credentials FROM bank_connections WHERE id=:id"
        ), {"id": connection_id}).scalar_one() == {"token": "old"}
        migration.upgrade()

    await sql_connection.run_sync(run)
