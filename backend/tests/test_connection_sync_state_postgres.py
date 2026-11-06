"""Connection retry state across real PostgreSQL transactions."""

import asyncio
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path
import uuid
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.models.account import Account
from app.models.asset import Asset
from app.models.bank_connection import BankConnection
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.providers.base import (
    AccountData, ConnectionData, HoldingData, ProviderUserActionRequired, SessionExpiredError,
    TransactionData,
)
from app.services import connection_service, fx_rate_service

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


async def test_reconnect_waits_for_failed_sync_status_commit(
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
                with pytest.raises(RuntimeError, match="temporary provider failure"):
                    await asyncio.wait_for(sync_task, timeout=5)
                await asyncio.wait_for(reconnect_task, timeout=5)

    await session.refresh(connection)
    assert connection.credentials == {"token": "new"}
    assert connection.status == "active"
    assert connection.last_sync_at is None
    assert connection.last_sync_error_account_id is None
    assert connection.sync_state_version == 2


async def test_waiting_expired_failure_is_not_lost_after_processing_failure(
    session, postgres_sessions, test_user, test_workspace,
):
    connection = await _connection(session, test_user, test_workspace)
    connection_id, user_id, workspace_id = connection.id, test_user.id, test_workspace.id
    provider = _provider()
    first_reading = asyncio.Event()
    fail_first = asyncio.Event()
    second_reading = asyncio.Event()
    fail_second = asyncio.Event()
    first_stamping = asyncio.Event()
    release_first_stamp = asyncio.Event()
    first_stamped = asyncio.Event()
    reads = 0

    async def get_accounts(credentials):
        nonlocal reads
        reads += 1
        if reads == 1:
            first_reading.set()
            await fail_first.wait()
            raise RuntimeError("temporary processing failure")
        second_reading.set()
        await fail_second.wait()
        raise SessionExpiredError("consent expired")

    provider.get_accounts.side_effect = get_accounts
    update_status = connection_service._set_sync_status_if_current
    async with postgres_sessions() as first_session, postgres_sessions() as second_session:
        async def ordered_status_update(sync_session, *args, **kwargs):
            if sync_session is first_session:
                first_stamping.set()
                await release_first_stamp.wait()
                await update_status(sync_session, *args, **kwargs)
                first_stamped.set()
            else:
                await first_stamped.wait()
                await update_status(sync_session, *args, **kwargs)

        with patch.object(connection_service, "get_provider", return_value=provider), patch.object(
            connection_service, "_set_sync_status_if_current", side_effect=ordered_status_update,
        ):
            first_task = asyncio.create_task(connection_service.sync_connection(
                first_session, connection_id, workspace_id, user_id,
            ))
            await asyncio.wait_for(first_reading.wait(), timeout=5)
            second_task = asyncio.create_task(connection_service.sync_connection(
                second_session, connection_id, workspace_id, user_id,
            ))
            fail_first.set()
            await asyncio.wait_for(first_stamping.wait(), timeout=5)
            try:
                await asyncio.wait_for(second_reading.wait(), timeout=0.1)
                second_read_before_status = True
            except TimeoutError:
                second_read_before_status = False
            finally:
                release_first_stamp.set()
                fail_second.set()
                results = await asyncio.wait_for(
                    asyncio.gather(first_task, second_task, return_exceptions=True), timeout=5,
                )

    assert isinstance(results[0], RuntimeError)
    assert isinstance(results[1], SessionExpiredError)
    await session.refresh(connection)
    assert connection.status == "expired"
    assert connection.sync_state_version == 2
    assert not second_read_before_status


@pytest.mark.parametrize("newer_operation", ["sync", "reconnect"])
async def test_stale_failure_guard_keeps_newer_committed_result(
    session, postgres_sessions, test_user, test_workspace, newer_operation,
):
    connection = await _connection(session, test_user, test_workspace)
    connection_id, user_id, workspace_id = connection.id, test_user.id, test_workspace.id
    stale_version = connection.sync_state_version
    provider = _provider()
    with patch.object(connection_service, "get_provider", return_value=provider):
        if newer_operation == "sync":
            await connection_service.sync_connection(session, connection_id, workspace_id, user_id)
        else:
            await connection_service.handle_oauth_callback(
                session, workspace_id, user_id, "setup-token",
                provider_name="test", reconnect_connection_id=connection_id,
            )
    newer_credentials = dict(connection.credentials)
    newer_sync_at = connection.last_sync_at
    async with postgres_sessions() as stale_session:
        await connection_service._set_sync_status_if_current(
            stale_session, connection_id, stale_version, "expired",
        )
        await stale_session.commit()

    await session.refresh(connection)
    assert connection.status == "active"
    assert connection.sync_state_version == stale_version + 1
    assert connection.credentials == newer_credentials
    assert connection.last_sync_at == newer_sync_at


async def test_user_action_refresh_commits_metadata_and_status_once(
    session, test_user, test_workspace,
):
    connection = await _connection(session, test_user, test_workspace)
    connection_id = connection.id
    provider = _provider()
    provider.get_institution_logo.return_value = "https://example.invalid/bank-logo.png"
    provider.trigger_refresh.return_value = "needs_user_action"
    with patch.object(connection_service, "get_provider", return_value=provider):
        with pytest.raises(ProviderUserActionRequired):
            await connection_service.sync_connection(
                session, connection_id, test_workspace.id, test_user.id,
                trigger_provider_refresh=True,
            )

    await session.refresh(connection)
    assert connection.status == "error"
    assert connection.sync_state_version == 1
    assert connection.credentials == {"token": "old-refreshed"}
    assert connection.logo_url == "https://example.invalid/bank-logo.png"
    assert connection.settings["last_provider_refresh_at"]


async def test_fx_fetch_retains_sync_lock_and_failed_import_rolls_back(
    session, postgres_sessions, test_user, test_workspace,
):
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    await session.commit()
    connection = await _connection(session, test_user, test_workspace)
    connection_id, user_id, workspace_id = connection.id, test_user.id, test_workspace.id
    provider = _provider()
    target = date(2025, 6, 15)
    provider.get_accounts.return_value = [AccountData(
        external_id="fx-account", name="Euro account", type="checking",
        balance=Decimal("100"), currency="EUR",
    )]
    provider.get_transactions.return_value = [TransactionData(
        external_id="fx-purchase", description="FX purchase", amount=Decimal("12"),
        currency="EUR", date=target, type="debit",
    )]
    imported = asyncio.Event()
    release = asyncio.Event()

    async def fail_after_conversion(sync_session, account):
        # Reaching opening-balance reconciliation means the real FX stamping
        # path has fetched rates and imported the transaction already.
        imported.set()
        await release.wait()
        await sync_session.execute(text("SELECT 1 / 0"))

    async with postgres_sessions() as sync_session, postgres_sessions() as reconnect_session:
        with patch.object(connection_service, "get_provider", return_value=provider), patch.object(
            connection_service, "sync_opening_balance_for_connected_account",
            side_effect=fail_after_conversion,
        ), patch.object(
            fx_rate_service._provider, "fetch_historical",
            new=AsyncMock(return_value={"EUR": Decimal("2")}),
        ) as fetch_rates:
            sync_task = asyncio.create_task(connection_service.sync_connection(
                sync_session, connection_id, workspace_id, user_id,
            ))
            await asyncio.wait_for(imported.wait(), timeout=5)
            reconnect_task = asyncio.create_task(connection_service.handle_oauth_callback(
                reconnect_session, workspace_id, user_id, "setup-token",
                provider_name="test", reconnect_connection_id=connection_id,
            ))
            try:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(reconnect_task), timeout=0.1)
                provider.handle_oauth_callback.assert_not_awaited()
                fetch_rates.assert_awaited_once_with(target)
                async with postgres_sessions() as observer:
                    assert await observer.scalar(select(func.count()).select_from(FxRate)) == 0
            finally:
                release.set()
                with pytest.raises(DBAPIError):
                    await asyncio.wait_for(sync_task, timeout=5)
                await asyncio.wait_for(reconnect_task, timeout=5)

    async with postgres_sessions() as observer:
        assert await observer.scalar(select(func.count()).select_from(FxRate)) == 0
        assert await observer.scalar(select(func.count()).select_from(Transaction)) == 0
        assert await observer.scalar(select(func.count()).select_from(Account)) == 0
        latest = await observer.get(BankConnection, connection_id)
        assert latest is not None and latest.status == "active"
        assert latest.credentials == {"token": "new"}
        assert latest.sync_state_version == 2


async def test_standalone_fx_fetch_still_commits_by_default(session, postgres_sessions):
    target = date(2025, 6, 15)
    with patch.object(
        fx_rate_service._provider, "fetch_historical",
        new=AsyncMock(return_value={"EUR": Decimal("2")}),
    ):
        assert await fx_rate_service.sync_rates(session, target) == 1

    async with postgres_sessions() as observer:
        rate = await observer.scalar(select(FxRate).where(FxRate.quote_currency == "EUR"))
        assert rate is not None and rate.rate == Decimal("2")


async def test_sync_state_migration_defaults_and_round_trip(
    session, test_user, test_workspace, monkeypatch,
):
    connection = await _connection(session, test_user, test_workspace)
    connection_id = connection.id
    path = Path(__file__).resolve().parents[1] / "alembic/versions/106_bank_connection_sync_state.py"
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


async def test_oauth_fx_import_rolls_back_on_later_database_error(
    session, postgres_sessions, test_user, test_workspace,
):
    test_user.preferences = {**(test_user.preferences or {}), "currency_display": "USD"}
    await session.commit()
    user_id, workspace_id = test_user.id, test_workspace.id
    provider = _provider()
    target = date(2025, 6, 15)
    provider.handle_oauth_callback.return_value.accounts = [AccountData(
        external_id="oauth-fx-account", name="Euro account", type="checking",
        balance=Decimal("100"), currency="EUR",
    )]
    provider.get_transactions.return_value = [TransactionData(
        external_id="oauth-fx-purchase", description="FX purchase", amount=Decimal("12"),
        currency="EUR", date=target, type="debit",
    )]

    async def fail_after_conversion(import_session, account):
        # Exercise real FX writes before an actual database failure, not a mock
        # of the stamping helper. Nothing may be visible to another session.
        async with postgres_sessions() as observer:
            assert await observer.scalar(select(func.count()).select_from(BankConnection)) == 0
            assert await observer.scalar(select(func.count()).select_from(FxRate)) == 0
            assert await observer.scalar(select(func.count()).select_from(Transaction)) == 0
        await import_session.execute(text("SELECT 1 / 0"))

    async with postgres_sessions() as import_session:
        with patch.object(connection_service, "get_provider", return_value=provider), patch.object(
            connection_service, "sync_opening_balance_for_connected_account",
            side_effect=fail_after_conversion,
        ), patch.object(
            fx_rate_service._provider, "fetch_historical",
            new=AsyncMock(return_value={"EUR": Decimal("2")}),
        ) as fetch_rates:
            with pytest.raises(DBAPIError):
                await connection_service.handle_oauth_callback(
                    import_session, workspace_id, user_id, "setup-token",
                    provider_name="test", sync_assets=False,
                )
            await import_session.rollback()
            fetch_rates.assert_awaited_once_with(target)

    async with postgres_sessions() as observer:
        for model in (BankConnection, Account, Transaction, FxRate):
            assert await observer.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("operation", ["reconnect", "delete"])
async def test_connection_mutation_times_out_without_disturbing_sync(
    session, postgres_sessions, test_user, test_workspace, operation,
):
    connection = await _connection(session, test_user, test_workspace)
    connection_id, user_id, workspace_id = connection.id, test_user.id, test_workspace.id
    provider = _provider()
    syncing, release = asyncio.Event(), asyncio.Event()

    async def get_accounts(credentials):
        syncing.set()
        await release.wait()
        return []

    provider.get_accounts.side_effect = get_accounts
    async with postgres_sessions() as sync_session, postgres_sessions() as mutation_session:
        with patch.object(connection_service, "get_provider", return_value=provider):
            sync_task = asyncio.create_task(connection_service.sync_connection(
                sync_session, connection_id, workspace_id, user_id,
            ))
            await asyncio.wait_for(syncing.wait(), timeout=5)
            try:
                # A wrong workspace must not wait on or reveal the locked row.
                assert await asyncio.wait_for(connection_service.delete_connection(
                    mutation_session, connection_id, uuid.uuid4(),
                ), timeout=0.5) is False
                await mutation_session.rollback()
                if operation == "reconnect":
                    mutation = connection_service.handle_oauth_callback(
                        mutation_session, workspace_id, user_id, "setup-token",
                        provider_name="test", reconnect_connection_id=connection_id,
                    )
                else:
                    mutation = connection_service.delete_connection(
                        mutation_session, connection_id, workspace_id,
                    )
                with pytest.raises(connection_service.ConnectionSyncInProgressError):
                    await asyncio.wait_for(mutation, timeout=3)
                provider.handle_oauth_callback.assert_not_awaited()
                assert not sync_task.done()
                # The timeout must roll back PostgreSQL's aborted transaction
                # and must not leak its transaction-local timeout to the pool.
                assert await mutation_session.scalar(text("SHOW lock_timeout")) == "0"
                unchanged = await mutation_session.get(BankConnection, connection_id)
                assert unchanged.credentials == {"token": "old"}
                assert unchanged.sync_state_version == 0
                await mutation_session.rollback()
            finally:
                release.set()
                await asyncio.wait_for(sync_task, timeout=5)

            # The same operation can succeed after the sync releases its lock.
            if operation == "reconnect":
                reconnected = await connection_service.handle_oauth_callback(
                    mutation_session, workspace_id, user_id, "setup-token",
                    provider_name="test", reconnect_connection_id=connection_id,
                )
                assert reconnected.credentials == {"token": "new"}
                assert reconnected.sync_state_version == 2
            else:
                assert await connection_service.delete_connection(
                    mutation_session, connection_id, workspace_id,
                ) is True
                assert await mutation_session.get(BankConnection, connection_id) is None


async def test_bounded_connection_lock_restores_previous_timeout(
    session, postgres_sessions, test_user, test_workspace,
):
    connection = await _connection(session, test_user, test_workspace)
    connection_id, workspace_id = connection.id, test_workspace.id
    await session.execute(text("SET LOCAL lock_timeout = '3s'"))
    locked = await connection_service._get_connection_for_mutation(
        session, connection_id, workspace_id,
    )
    assert locked is not None and locked.id == connection_id
    assert await session.scalar(text("SHOW lock_timeout")) == "3s"
    # Restoring the timeout must not release the row lock itself.
    async with postgres_sessions() as other:
        with pytest.raises(connection_service.ConnectionSyncInProgressError):
            await asyncio.wait_for(connection_service._get_connection_for_mutation(
                other, connection_id, workspace_id,
            ), timeout=3)


async def test_delete_waits_for_sync_and_archives_its_new_holdings(
    session, postgres_sessions, test_user, test_workspace,
):
    connection = await _connection(session, test_user, test_workspace)
    connection.settings = {"sync_assets": True}
    await session.commit()
    connection_id, user_id, workspace_id = connection.id, test_user.id, test_workspace.id
    provider = _provider()
    provider.get_holdings.return_value = [HoldingData(
        external_id="new-holding", name="New holding", currency="USD",
        current_value=Decimal("100"),
    )]
    importing, release = asyncio.Event(), asyncio.Event()
    sync_holdings = connection_service._sync_holdings

    async def pause_before_holdings(*args, **kwargs):
        importing.set()
        await release.wait()
        await sync_holdings(*args, **kwargs)

    async with postgres_sessions() as sync_session, postgres_sessions() as delete_session:
        with patch.object(connection_service, "get_provider", return_value=provider), patch.object(
            connection_service, "_sync_holdings", side_effect=pause_before_holdings,
        ):
            sync_task = asyncio.create_task(connection_service.sync_connection(
                sync_session, connection_id, workspace_id, user_id,
            ))
            await asyncio.wait_for(importing.wait(), timeout=5)
            delete_task = asyncio.create_task(connection_service.delete_connection(
                delete_session, connection_id, workspace_id,
            ))
            try:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(delete_task), timeout=0.1)
            finally:
                release.set()
                await asyncio.wait_for(sync_task, timeout=5)
                assert await asyncio.wait_for(delete_task, timeout=5) is True

    async with postgres_sessions() as observer:
        assert await observer.get(BankConnection, connection_id) is None
        holding = await observer.scalar(select(Asset).where(Asset.external_id == "new-holding"))
        assert holding is not None and holding.is_archived
        assert holding.connection_id is None


async def test_bounded_connection_lock_does_not_mask_other_database_errors(
    session, test_user, test_workspace,
):
    async def fail_read(*args, **kwargs):
        await session.execute(text("SELECT 1 / 0"))

    with patch.object(connection_service, "get_connection", side_effect=fail_read):
        with pytest.raises(DBAPIError) as failure:
            await connection_service._get_connection_for_mutation(
                session, uuid.uuid4(), test_workspace.id,
            )
    assert getattr(failure.value.orig, "sqlstate", None) == "22012"
