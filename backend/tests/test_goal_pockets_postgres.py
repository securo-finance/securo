"""Pocket locking and migration gates in disposable PostgreSQL schemas."""

import asyncio
import importlib.util
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.mortgage_payments import _get_payment
from app.models.account import Account
from app.models.category import Category
from app.models.goal import Goal, GoalAllocation
from app.models.import_log import ImportLog
from app.models.mortgage_payment_allocation import MortgagePaymentAllocation
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.schemas.goal import GoalAllocationInput
from app.schemas.transaction import TransactionImport, TransactionUpdate, TransferCreate
from app.services import category_service, import_service
from app.services.admin_service import delete_user
from app.services.goal_allocation_service import (
    _transaction_allocations_for_update,
    replace_transaction_allocations,
)
from app.services.goal_service import _account_reserved_total, _pocket_current_amount
from app.services.transaction_service import create_transfer, delete_transaction, update_transaction

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Reuse the shared data fixtures in a fresh PostgreSQL schema."""


@pytest_asyncio.fixture
async def pocket_state(session, test_user, test_workspace, test_account):
    goal = Goal(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="PostgreSQL pocket",
        target_amount=Decimal("1500"),
        currency=test_account.currency,
        tracking_type="pocket",
        account_id=test_account.id,
    )
    transaction = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="Pocket deposit",
        amount=Decimal("200"),
        currency=test_account.currency,
        date=date(2026, 1, 1),
        type="credit",
        source="manual",
    )
    session.add_all([goal, transaction])
    await session.commit()
    return test_user, test_workspace, test_account, goal, transaction


async def _wait_for_lock(observer, pid):
    """Prove overlap without relying on arbitrary sleeps or query mocks."""
    async with asyncio.timeout(10):
        while not await observer.scalar(
            text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
        ):
            await asyncio.sleep(0.01)


async def test_allocation_lock_blocks_writes_without_locking_joined_goal(
    session,
    postgres_sessions,
    pocket_state,
):
    user, workspace, _, goal, transaction = pocket_state
    allocation = GoalAllocation(
        workspace_id=workspace.id,
        goal_id=goal.id,
        transaction_id=transaction.id,
        user_id=user.id,
        amount=Decimal("80"),
        source="transaction",
    )
    session.add(allocation)
    await session.commit()
    rows = await session.scalars(_transaction_allocations_for_update(workspace.id, transaction.id))
    assert list(rows) == [allocation]

    async with postgres_sessions() as writer:
        pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        await writer.scalar(select(Goal).where(Goal.id == goal.id).with_for_update(nowait=True))

        async def write():
            await writer.execute(
                update(GoalAllocation).where(GoalAllocation.id == allocation.id).values(amount=70)
            )
            await writer.commit()

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(write())
            await _wait_for_lock(session, pid)
            await session.commit()

    async with postgres_sessions() as fresh:
        assert await _pocket_current_amount(fresh, goal.id) == Decimal("70")


@pytest.mark.parametrize(
    ("model", "field", "value", "error"),
    [
        (Transaction, "status", "pending", "Only posted transactions"),
        (Transaction, "amount", Decimal("50"), "exceed the transaction amount"),
        (Goal, "status", "paused", "Only active pockets"),
        (Account, "currency", "USD", "same currency"),
    ],
    ids=["transaction-status", "transaction-amount", "goal-status", "account-currency"],
)
async def test_waiting_allocation_reloads_locked_state(
    postgres_sessions,
    pocket_state,
    model,
    field,
    value,
    error,
):
    user, workspace, account, goal, transaction = pocket_state
    row_id = {Transaction: transaction.id, Goal: goal.id, Account: account.id}[model]
    async with postgres_sessions() as stale, postgres_sessions() as writer:
        stale_transaction = await stale.get(Transaction, transaction.id)
        stale_row = await stale.get(model, row_id)
        assert getattr(stale_row, field) != value
        pid = await stale.scalar(text("SELECT pg_backend_pid()"))
        changed = await writer.scalar(select(model).where(model.id == row_id).with_for_update())
        setattr(changed, field, value)
        await writer.flush()

        async def assign():
            with pytest.raises(ValueError, match=error):
                await replace_transaction_allocations(
                    stale,
                    workspace.id,
                    user.id,
                    stale_transaction,
                    [GoalAllocationInput(goal_id=goal.id, amount=Decimal("100"))],
                )
            assert getattr(stale_row, field) == value
            await stale.rollback()

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(assign())
            await _wait_for_lock(writer, pid)
            await writer.commit()

    async with postgres_sessions() as fresh:
        assert await _pocket_current_amount(fresh, goal.id) == 0


@pytest.mark.parametrize("kind", ["credit", "debit"])
async def test_concurrent_allocations_preserve_available_and_pocket_balances(
    session,
    postgres_sessions,
    pocket_state,
    kind,
):
    user, workspace, account, goal, first = pocket_state
    amount = Decimal("1000" if kind == "credit" else "100")
    second_goal = goal
    if kind == "credit":
        second_goal = Goal(
            user_id=user.id,
            workspace_id=workspace.id,
            name="Second pocket",
            target_amount=amount,
            currency=account.currency,
            tracking_type="pocket",
            account_id=account.id,
        )
        session.add(second_goal)
    else:
        session.add(
            GoalAllocation(
                workspace_id=workspace.id,
                goal_id=goal.id,
                user_id=user.id,
                amount=Decimal("150"),
                source="opening",
            )
        )
    await session.commit()
    ready = asyncio.Queue()

    async def assign(goal_id):
        async with postgres_sessions() as worker:
            transaction = Transaction(
                user_id=user.id,
                workspace_id=workspace.id,
                account_id=account.id,
                description="Concurrent pocket allocation",
                amount=amount,
                currency=account.currency,
                date=first.date,
                type=kind,
                source="manual",
            )
            worker.add(transaction)
            await worker.flush()  # Both workers now hold the account FK's KEY SHARE lock.
            ready.put_nowait(await worker.scalar(text("SELECT pg_backend_pid()")))
            try:
                await replace_transaction_allocations(
                    worker,
                    workspace.id,
                    user.id,
                    transaction,
                    [GoalAllocationInput(goal_id=goal_id, amount=amount)],
                )
                await worker.commit()
                return None
            except ValueError as error:
                await worker.rollback()
                return str(error)

    async with postgres_sessions() as blocker:
        await blocker.scalar(
            select(Account).where(Account.id == account.id).with_for_update(key_share=True)
        )
        async with asyncio.timeout(20), asyncio.TaskGroup() as tasks:
            first_task = tasks.create_task(assign(goal.id))
            second_task = tasks.create_task(assign(second_goal.id))
            for _ in range(2):
                await _wait_for_lock(blocker, await ready.get())
            await blocker.commit()
    results = [first_task.result(), second_task.result()]
    assert results.count(None) == 1
    error = next(result for result in results if result is not None)
    assert ("available balance" if kind == "credit" else "reserved balance") in error.lower()
    async with postgres_sessions() as fresh:
        reserved = await _account_reserved_total(fresh, workspace.id, account.id)
        assert reserved == Decimal("1000" if kind == "credit" else "50")
        for pocket_id in {goal.id, second_goal.id}:
            assert await _pocket_current_amount(fresh, pocket_id) >= 0
        assert await fresh.scalar(select(Account.balance).where(Account.id == account.id)) == 1500


async def test_transaction_edit_sees_assignments_added_while_waiting(
    postgres_sessions,
    pocket_state,
):
    user, workspace, _, goal, transaction = pocket_state
    async with postgres_sessions() as stale, postgres_sessions() as writer:
        cached = await stale.get(Transaction, transaction.id)
        assert cached.goal_allocations == []
        pid = await stale.scalar(text("SELECT pg_backend_pid()"))
        await replace_transaction_allocations(
            writer,
            workspace.id,
            user.id,
            await writer.get(Transaction, transaction.id),
            [GoalAllocationInput(goal_id=goal.id, amount=150)],
        )

        async def edit():
            with pytest.raises(ValueError):
                await update_transaction(
                    stale,
                    transaction.id,
                    workspace.id,
                    user.id,
                    TransactionUpdate(amount=100),
                )
            await stale.rollback()

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(edit())
            await _wait_for_lock(writer, pid)
            await writer.commit()
    async with postgres_sessions() as fresh:
        assert (
            await fresh.scalar(select(Transaction.amount).where(Transaction.id == transaction.id))
            == 200
        )
        assert await _pocket_current_amount(fresh, goal.id) == 150


async def test_category_transfer_sees_assignments_added_while_waiting(
    session, postgres_sessions, pocket_state
):
    user, workspace, _, goal, transaction = pocket_state
    source = Category(user_id=user.id, workspace_id=workspace.id, name="Source")
    destination = Category(
        user_id=user.id, workspace_id=workspace.id, name="Ignored", is_ignored=True
    )
    session.add_all([source, destination])
    await session.flush()
    transaction.category_id = source.id
    await session.commit()
    source_id, destination_id, transaction_id, goal_id = (
        source.id, destination.id, transaction.id, goal.id
    )

    async with postgres_sessions() as stale, postgres_sessions() as writer:
        cached = await stale.get(Transaction, transaction_id)
        assert cached.goal_allocations == []
        pid = await stale.scalar(text("SELECT pg_backend_pid()"))
        await replace_transaction_allocations(
            writer, workspace.id, user.id,
            await writer.get(Transaction, transaction_id),
            [GoalAllocationInput(goal_id=goal_id, amount=150)],
        )

        async def transfer():
            with pytest.raises(ValueError, match="Ignored transactions"):
                await category_service.delete_category(
                    stale, source_id, workspace.id, transfer_to_id=destination_id
                )
            await stale.rollback()

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(transfer())
            await _wait_for_lock(writer, pid)
            await writer.commit()

    async with postgres_sessions() as fresh:
        assert await fresh.get(Category, source_id) is not None
        assert await fresh.scalar(
            select(Transaction.category_id).where(Transaction.id == transaction_id)
        ) == source_id
        assert await _pocket_current_amount(fresh, goal_id) == 150


async def test_deleting_credit_cannot_leave_spent_pocket_negative(session, pocket_state):
    user, workspace, account, goal, credit = pocket_state
    await replace_transaction_allocations(
        session,
        workspace.id,
        user.id,
        credit,
        [GoalAllocationInput(goal_id=goal.id, amount=200)],
    )
    debit = Transaction(
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        description="Spent pocket deposit",
        amount=Decimal("150"),
        currency=account.currency,
        date=credit.date,
        type="debit",
        source="manual",
    )
    session.add(debit)
    await session.flush()
    await replace_transaction_allocations(
        session,
        workspace.id,
        user.id,
        debit,
        [GoalAllocationInput(goal_id=goal.id, amount=150)],
    )
    await session.commit()
    credit_id, goal_id = credit.id, goal.id
    with pytest.raises(ValueError):
        await delete_transaction(session, credit_id, workspace.id)
    await session.rollback()
    assert await session.get(Transaction, credit_id) is not None
    assert await _pocket_current_amount(session, goal_id) == 50


async def test_concurrent_opposite_pocket_transfers_do_not_deadlock(
    session,
    postgres_sessions,
    pocket_state,
):
    user, workspace, account, goal, transaction = pocket_state
    destination = Account(
        user_id=user.id,
        workspace_id=workspace.id,
        connection_id=account.connection_id,
        name="Transfer destination",
        type="checking",
        balance=Decimal("1500"),
        currency=account.currency,
    )
    session.add(destination)
    await session.flush()
    destination_goal = Goal(
        user_id=user.id,
        workspace_id=workspace.id,
        name="Destination pocket",
        target_amount=Decimal("1500"),
        currency=account.currency,
        tracking_type="pocket",
        account_id=destination.id,
    )
    session.add(destination_goal)
    await session.flush()
    for pocket in (goal, destination_goal):
        session.add(
            GoalAllocation(
                workspace_id=workspace.id,
                goal_id=pocket.id,
                user_id=user.id,
                amount=Decimal("500"),
                source="opening",
            )
        )
    await session.commit()
    ready = asyncio.Queue()

    async def transfer(source, target, source_goal, target_goal):
        async with postgres_sessions() as worker:
            ready.put_nowait(await worker.scalar(text("SELECT pg_backend_pid()")))
            return await create_transfer(
                worker,
                workspace.id,
                user.id,
                TransferCreate(
                    from_account_id=source.id,
                    to_account_id=target.id,
                    amount=Decimal("100"),
                    date=transaction.date,
                    description="Opposite pocket transfers",
                    from_goal_allocations=[GoalAllocationInput(goal_id=source_goal.id, amount=100)],
                    to_goal_allocations=[GoalAllocationInput(goal_id=target_goal.id, amount=100)],
                ),
            )

    async with postgres_sessions() as blocker:
        await blocker.execute(
            select(Account.id)
            .where(Account.id.in_([account.id, destination.id]))
            .order_by(Account.id)
            .with_for_update(key_share=True)
        )
        async with asyncio.timeout(20), asyncio.TaskGroup() as tasks:
            first = tasks.create_task(transfer(account, destination, goal, destination_goal))
            second = tasks.create_task(transfer(destination, account, destination_goal, goal))
            for _ in range(2):
                await _wait_for_lock(blocker, await ready.get())
            await blocker.commit()
    assert first.result()[0].transfer_pair_id != second.result()[0].transfer_pair_id
    async with postgres_sessions() as fresh:
        for pocket in (goal, destination_goal):
            assert await _pocket_current_amount(fresh, pocket.id) == 500


def _import_rows(account, day, index):
    return [
        TransactionImport(
            description=f"Concurrent import {index}",
            amount=Decimal("10"),
            type="credit",
            currency=account.currency,
            date=day,
        )
    ]


async def _run_import(worker, user, workspace, account, day, index):
    return await import_service.import_transactions(
        worker,
        workspace.id,
        user.id,
        account.id,
        _import_rows(account, day, index),
        "csv",
        detected_format="csv",
        detect_duplicates=False,
    )


def _import_paused_after_log(postgres_sessions, holding, release):
    """A session that stops right after the ImportLog INSERT, locks held."""

    class PausedImportSession(AsyncSession):
        async def flush(self, objects=None):
            has_import_log = any(isinstance(row, ImportLog) for row in self.new)
            await super().flush(objects)
            if has_import_log:
                holding.set()
                await release.wait()

    return async_sessionmaker(
        postgres_sessions.kw["bind"], class_=PausedImportSession, expire_on_commit=False
    )


async def _backend_pid(session):
    return await session.scalar(text("SELECT pg_backend_pid()"))


@pytest.mark.parametrize("connected", [False, True], ids=["manual", "connected"])
async def test_concurrent_imports_into_one_account_serialize(
    session,
    postgres_sessions,
    pocket_state,
    connected,
):
    """The second import waits for the first, and neither deadlocks on the
    KEY SHARE that the ImportLog foreign key takes on the account."""
    user, workspace, account, goal, transaction = pocket_state
    if not connected:
        account.connection_id = None
    session.add(
        GoalAllocation(
            workspace_id=workspace.id,
            goal_id=goal.id,
            user_id=user.id,
            amount=Decimal("100"),
            source="opening",
        )
    )
    await session.commit()
    holding, release = asyncio.Event(), asyncio.Event()
    paused = _import_paused_after_log(postgres_sessions, holding, release)

    async with paused() as first, postgres_sessions() as second, postgres_sessions() as observer:
        pid = await _backend_pid(second)
        async with asyncio.timeout(20):
            async with asyncio.TaskGroup() as tasks:
                first_run = tasks.create_task(
                    _run_import(first, user, workspace, account, transaction.date, 0)
                )
                await holding.wait()
                second_run = tasks.create_task(
                    _run_import(second, user, workspace, account, transaction.date, 1)
                )
                await _wait_for_lock(observer, pid)
                release.set()
    assert [first_run.result()[:3], second_run.result()[:3]] == [(1, 0, 0), (1, 0, 0)]
    async with postgres_sessions() as fresh:
        rows = list(await fresh.scalars(select(Transaction).where(Transaction.source == "csv")))
        assert len(rows) == 2
        assert all(row.import_id is not None for row in rows)
        assert await _pocket_current_amount(fresh, goal.id) == 100


@pytest_asyncio.fixture
async def edit_targets(session, pocket_state):
    """A pocket-funded row to edit and an unassigned row to delete."""
    user, workspace, account, goal, transaction = pocket_state
    await replace_transaction_allocations(
        session,
        workspace.id,
        user.id,
        await session.get(Transaction, transaction.id),
        [GoalAllocationInput(goal_id=goal.id, amount=100)],
    )
    spare = Transaction(
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        description="Unassigned row",
        amount=Decimal("30"),
        currency=account.currency,
        date=transaction.date,
        type="debit",
        source="manual",
    )
    session.add(spare)
    await session.commit()
    return spare


async def _edit(session, operation, workspace, user, transaction, spare):
    if operation == "update":
        await update_transaction(
            session, transaction.id, workspace.id, user.id, TransactionUpdate(amount=150)
        )
    else:
        assert await delete_transaction(session, spare.id, workspace.id)
    await session.commit()


async def _assert_edit_and_import_landed(postgres_sessions, operation, goal, transaction, spare):
    async with postgres_sessions() as fresh:
        assert len(list(await fresh.scalars(
            select(Transaction).where(Transaction.source == "csv")
        ))) == 1
        if operation == "update":
            assert await fresh.scalar(
                select(Transaction.amount).where(Transaction.id == transaction.id)
            ) == 150
        else:
            assert await fresh.get(Transaction, spare.id) is None
        assert await _pocket_current_amount(fresh, goal.id) == 100


@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_transaction_edit_waits_for_running_import(
    postgres_sessions, pocket_state, edit_targets, operation
):
    user, workspace, account, goal, transaction = pocket_state
    holding, release = asyncio.Event(), asyncio.Event()
    paused = _import_paused_after_log(postgres_sessions, holding, release)

    async with paused() as importer, postgres_sessions() as editor, postgres_sessions() as observer:
        pid = await _backend_pid(editor)
        async with asyncio.timeout(20):
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(
                    _run_import(importer, user, workspace, account, transaction.date, 0)
                )
                await holding.wait()
                tasks.create_task(
                    _edit(editor, operation, workspace, user, transaction, edit_targets)
                )
                await _wait_for_lock(observer, pid)
                release.set()
    await _assert_edit_and_import_landed(
        postgres_sessions, operation, goal, transaction, edit_targets
    )


def _pause_after_edit_locks(monkeypatch, holding, release):
    """Stop update/delete right after they took their account and row locks."""
    from app.services import transaction_service

    original = transaction_service._lock_mutated_transactions

    async def locked_then_paused(*args, **kwargs):
        rows = await original(*args, **kwargs)
        holding.set()
        await release.wait()
        return rows

    monkeypatch.setattr(transaction_service, "_lock_mutated_transactions", locked_then_paused)


@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_import_waits_for_running_transaction_edit(
    monkeypatch, postgres_sessions, pocket_state, edit_targets, operation
):
    user, workspace, account, goal, transaction = pocket_state
    holding, release = asyncio.Event(), asyncio.Event()
    _pause_after_edit_locks(monkeypatch, holding, release)

    async with postgres_sessions() as editor, postgres_sessions() as importer, postgres_sessions() as observer:
        pid = await _backend_pid(importer)
        async with asyncio.timeout(20):
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(
                    _edit(editor, operation, workspace, user, transaction, edit_targets)
                )
                await holding.wait()
                tasks.create_task(
                    _run_import(importer, user, workspace, account, transaction.date, 0)
                )
                await _wait_for_lock(observer, pid)
                release.set()
    await _assert_edit_and_import_landed(
        postgres_sessions, operation, goal, transaction, edit_targets
    )


SYNCED_EXTERNAL_ID = "pocket-sync-1"


@pytest_asyncio.fixture(params=[False, True], ids=["new-opening", "existing-opening"])
async def synced_row(request, session, pocket_state):
    """A pending synced row the next sync will post, in the pocket's account.

    With an existing opening balance the sync reconciles that row at the end,
    after it posted this one, which once reached for the account lock late.
    """
    user, workspace, account, goal, _ = pocket_state
    if request.param:
        session.add(
            Transaction(
                user_id=user.id,
                workspace_id=workspace.id,
                account_id=account.id,
                description="Saldo inicial",
                amount=Decimal("1"),
                currency=account.currency,
                date=date(2020, 1, 1),
                type="credit",
                source="opening_balance",
            )
        )
    row = Transaction(
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        external_id=SYNCED_EXTERNAL_ID,
        description="CARD PURCHASE",
        amount=Decimal("40"),
        currency=account.currency,
        date=date.today(),
        type="debit",
        source="sync",
        status="pending",
    )
    session.add(row)
    await session.commit()
    return row


def _sync_provider(account):
    from unittest.mock import AsyncMock

    from app.providers.base import AccountData, TransactionData

    provider = AsyncMock()
    provider.rereads_by_date = False
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_institution_logo = AsyncMock(return_value=None)
    provider.get_accounts = AsyncMock(return_value=[
        AccountData(
            external_id=account.external_id, name=account.name, type=account.type,
            balance=Decimal("1500"), currency=account.currency,
        ),
    ])
    provider.get_bills = AsyncMock(return_value=[])
    provider.get_holdings = AsyncMock(return_value=[])
    provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id=SYNCED_EXTERNAL_ID, description="CARD PURCHASE",
            amount=Decimal("40"), date=date.today(), type="debit",
            currency=account.currency, status="posted",
        ),
        TransactionData(
            external_id="pocket-sync-2", description="NEW PURCHASE",
            amount=Decimal("5"), date=date.today(), type="debit",
            currency=account.currency, status="posted",
        ),
    ])
    provider.get_transactions_by_date = AsyncMock(return_value=[])
    return provider


async def _run_sync(worker, account, workspace, user):
    from unittest.mock import AsyncMock, patch

    from app.services.connection_service import sync_connection

    with patch("app.services.connection_service.get_provider", return_value=_sync_provider(account)), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(worker, account.connection_id, workspace.id, user.id)


async def _edit_synced(editor, workspace, user, row):
    await update_transaction(
        editor, row.id, workspace.id, user.id, TransactionUpdate(notes="edited during sync")
    )
    await editor.commit()


async def _assert_edit_and_sync_landed(postgres_sessions, goal, row):
    async with postgres_sessions() as fresh:
        stored = await fresh.get(Transaction, row.id)
        assert stored.notes == "edited during sync"
        assert stored.status == "posted"
        assert await fresh.scalar(
            select(Transaction.id).where(Transaction.external_id == "pocket-sync-2")
        ) is not None
        assert await _pocket_current_amount(fresh, goal.id) == 100


async def test_sync_waits_for_running_transaction_edit(
    monkeypatch, postgres_sessions, pocket_state, edit_targets, synced_row
):
    user, workspace, account, goal, _ = pocket_state
    holding, release = asyncio.Event(), asyncio.Event()
    _pause_after_edit_locks(monkeypatch, holding, release)

    async with postgres_sessions() as editor, postgres_sessions() as syncer, postgres_sessions() as observer:
        pid = await _backend_pid(syncer)
        async with asyncio.timeout(30):
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(_edit_synced(editor, workspace, user, synced_row))
                await holding.wait()
                tasks.create_task(_run_sync(syncer, account, workspace, user))
                await _wait_for_lock(observer, pid)
                release.set()
    await _assert_edit_and_sync_landed(postgres_sessions, goal, synced_row)


async def test_transaction_edit_waits_for_running_sync(
    postgres_sessions, pocket_state, edit_targets, synced_row
):
    user, workspace, account, goal, _ = pocket_state
    holding, release = asyncio.Event(), asyncio.Event()

    class PausedSyncSession(AsyncSession):
        async def flush(self, objects=None):
            posts_row = any(
                isinstance(row, Transaction) and row.id == synced_row.id and row.status == "posted"
                for row in self.dirty
            )
            await super().flush(objects)
            if posts_row and not holding.is_set():
                holding.set()
                await release.wait()

    paused = async_sessionmaker(
        postgres_sessions.kw["bind"], class_=PausedSyncSession, expire_on_commit=False
    )
    async with paused() as syncer, postgres_sessions() as editor, postgres_sessions() as observer:
        pid = await _backend_pid(editor)
        async with asyncio.timeout(30):
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(_run_sync(syncer, account, workspace, user))
                await holding.wait()
                tasks.create_task(_edit_synced(editor, workspace, user, synced_row))
                await _wait_for_lock(observer, pid)
                release.set()
    await _assert_edit_and_sync_landed(postgres_sessions, goal, synced_row)


async def test_mortgage_breakdown_takes_principal_entry_before_higher_payment(
    session, postgres_sessions, pocket_state
):
    """Rule application locks rows in ID order, so a breakdown save must too."""
    user, workspace, account, _, _ = pocket_state
    principal_id = uuid.UUID(int=1)
    payment_id = uuid.UUID(int=2**128 - 1)
    session.add_all(
        Transaction(
            id=transaction_id,
            user_id=user.id,
            workspace_id=workspace.id,
            account_id=account.id,
            description=description,
            amount=Decimal("50"),
            currency=account.currency,
            date=date(2026, 1, 1),
            type="debit",
            source="manual",
        )
        for transaction_id, description in ((principal_id, "Principal"), (payment_id, "Payment"))
    )
    await session.flush()
    session.add(
        MortgagePaymentAllocation(
            workspace_id=workspace.id,
            payment_transaction_id=payment_id,
            loan_account_id=account.id,
            principal_amount=Decimal("40"),
            interest_amount=Decimal("10"),
            loan_transaction_id=principal_id,
        )
    )
    await session.commit()

    async with postgres_sessions() as saver, postgres_sessions() as probe:
        pid = await saver.scalar(text("SELECT pg_backend_pid()"))
        await session.execute(
            select(Transaction.id).where(Transaction.id == principal_id).with_for_update()
        )

        async with asyncio.TaskGroup() as tasks:
            save = tasks.create_task(_get_payment(saver, payment_id, workspace.id, lock=True))
            await _wait_for_lock(probe, pid)
            await probe.execute(
                select(Transaction.id).where(Transaction.id == payment_id).with_for_update(nowait=True)
            )
            await probe.rollback()
            await session.commit()

        assert save.result() is not None
        await saver.rollback()


async def test_waiting_mortgage_breakdown_locks_principal_entry_of_earlier_save(
    session, postgres_sessions, pocket_state
):
    """A save queued behind another must lock the principal entry that one inserts."""
    user, workspace, account, _, _ = pocket_state
    old_principal_id = uuid.UUID(int=1)
    new_principal_id = uuid.UUID(int=3)
    payment_id = uuid.UUID(int=2**128 - 1)

    def entry(transaction_id, description):
        return Transaction(
            id=transaction_id,
            user_id=user.id,
            workspace_id=workspace.id,
            account_id=account.id,
            description=description,
            amount=Decimal("50"),
            currency=account.currency,
            date=date(2026, 1, 1),
            type="debit",
            source="manual",
        )

    def breakdown(principal_id):
        return MortgagePaymentAllocation(
            workspace_id=workspace.id,
            payment_transaction_id=payment_id,
            loan_account_id=account.id,
            principal_amount=Decimal("40"),
            interest_amount=Decimal("10"),
            loan_transaction_id=principal_id,
        )

    session.add_all([entry(old_principal_id, "Principal"), entry(payment_id, "Payment")])
    await session.flush()
    session.add(breakdown(old_principal_id))
    await session.commit()

    async with (
        postgres_sessions() as first,
        postgres_sessions() as second,
        postgres_sessions() as probe,
    ):
        pid = await second.scalar(text("SELECT pg_backend_pid()"))
        assert await _get_payment(first, payment_id, workspace.id, lock=True) is not None
        await first.execute(
            delete(MortgagePaymentAllocation).where(
                MortgagePaymentAllocation.payment_transaction_id == payment_id
            )
        )
        await first.execute(delete(Transaction).where(Transaction.id == old_principal_id))
        first.add(entry(new_principal_id, "New principal"))
        await first.flush()
        first.add(breakdown(new_principal_id))
        await first.flush()

        async with asyncio.TaskGroup() as tasks:
            save = tasks.create_task(_get_payment(second, payment_id, workspace.id, lock=True))
            await _wait_for_lock(probe, pid)
            await first.commit()

        assert save.result() is not None
        with pytest.raises(DBAPIError):
            await probe.execute(
                select(Transaction.id)
                .where(Transaction.id == new_principal_id)
                .with_for_update(nowait=True)
            )
        await probe.rollback()
        await second.rollback()


async def test_deleting_allocation_actor_preserves_reservations(
    session,
    postgres_sessions,
    pocket_state,
):
    user, workspace, account, goal, _ = pocket_state
    editor = User(email="pocket-editor@example.com", hashed_password="unused")
    session.add(editor)
    await session.flush()
    session.add_all(
        [
            WorkspaceMember(workspace_id=workspace.id, user_id=editor.id, role="editor"),
            GoalAllocation(
                workspace_id=workspace.id,
                goal_id=goal.id,
                user_id=editor.id,
                amount=Decimal("400"),
                source="adjustment",
            ),
        ]
    )
    await session.commit()
    assert await delete_user(session, editor.id, user.id)
    async with postgres_sessions() as fresh:
        allocation = await fresh.scalar(
            select(GoalAllocation).where(GoalAllocation.goal_id == goal.id)
        )
        assert allocation.user_id is None
        assert await _pocket_current_amount(fresh, goal.id) == 400
        assert await _account_reserved_total(fresh, workspace.id, account.id) == 400
        assert await fresh.get(User, editor.id) is None


async def test_deleting_user_cannot_leave_pocket_funded_by_their_deposit_negative(
    session, postgres_sessions, pocket_state
):
    user, workspace, account, goal, _ = pocket_state
    editor = User(email="pocket-depositor@example.com", hashed_password="unused")
    session.add(editor)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=editor.id, role="editor"))

    def entry(owner, description, amount, type_):
        return Transaction(
            user_id=owner.id,
            workspace_id=workspace.id,
            account_id=account.id,
            description=description,
            amount=Decimal(amount),
            currency=account.currency,
            date=date(2026, 1, 1),
            type=type_,
            source="manual",
        )

    deposit = entry(editor, "Editor deposit", "100", "credit")
    spending = entry(user, "Owner spending", "60", "debit")
    session.add_all([deposit, spending])
    await session.flush()
    for transaction, amount in ((deposit, 100), (spending, 60)):
        await replace_transaction_allocations(
            session,
            workspace.id,
            user.id,
            transaction,
            [GoalAllocationInput(goal_id=goal.id, amount=amount)],
        )
    await session.commit()
    editor_id, deposit_id, spending_id, goal_id = editor.id, deposit.id, spending.id, goal.id
    owner_id = user.id

    with pytest.raises(ValueError):
        await delete_user(session, editor_id, owner_id)
    await session.rollback()
    async with postgres_sessions() as fresh:
        assert await fresh.get(User, editor_id) is not None
        assert await fresh.get(Transaction, deposit_id) is not None
        assert await _pocket_current_amount(fresh, goal_id) == 40

    await session.execute(
        GoalAllocation.__table__.delete().where(GoalAllocation.transaction_id == spending_id)
    )
    await session.commit()
    assert await delete_user(session, editor_id, owner_id)
    async with postgres_sessions() as fresh:
        assert await fresh.get(Transaction, deposit_id) is None
        assert await _pocket_current_amount(fresh, goal_id) == 0


def _migrate(connection, direction):
    path = Path(__file__).resolve().parent.parent / "alembic/versions/128_goal_pockets.py"
    spec = importlib.util.spec_from_file_location("goal_pockets_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert (migration.revision, migration.down_revision) == ("128", "127")
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration, direction)()


async def test_pocket_migration_upgrade_constraints_and_downgrade(session, pocket_state):
    user, workspace, _, goal, transaction = pocket_state
    connection = await session.connection()
    schema = connection.sync_connection.get_execution_options()["schema_translate_map"][None]
    await connection.run_sync(_migrate, "downgrade")
    await connection.run_sync(_migrate, "upgrade")

    def check_schema(conn):
        inspector = inspect(conn)
        columns = {
            column["name"]: column for column in inspector.get_columns("goal_allocations", schema)
        }
        assert columns["user_id"]["nullable"]
        assert {index["name"] for index in inspector.get_indexes("goal_allocations", schema)} >= {
            "ix_goal_allocations_workspace_id",
            "ix_goal_allocations_goal_id",
            "ix_goal_allocations_transaction_id",
        }
        foreign_keys = {
            tuple(fk["constrained_columns"]): fk["options"].get("ondelete")
            for fk in inspector.get_foreign_keys("goal_allocations", schema)
        }
        assert foreign_keys == {
            ("workspace_id",): "CASCADE",
            ("goal_id",): "CASCADE",
            ("transaction_id",): "CASCADE",
            ("user_id",): "SET NULL",
        }
        assert {
            item["name"] for item in inspector.get_check_constraints("goal_allocations", schema)
        } == {
            "ck_goal_allocations_amount_nonzero",
            "ck_goal_allocations_source",
            "ck_goal_allocations_source_transaction",
        }

    await connection.run_sync(check_schema)
    for source, transaction_id in [
        ("opening", None),
        ("adjustment", None),
        ("transaction", transaction.id),
    ]:
        session.add(
            GoalAllocation(
                workspace_id=workspace.id,
                goal_id=goal.id,
                user_id=user.id,
                amount=Decimal("25"),
                source=source,
                transaction_id=transaction_id,
            )
        )
    await session.flush()
    for amount, source, transaction_id in [
        (0, "adjustment", None),
        (10, "invalid", None),
        (10, "transaction", None),
        (10, "opening", transaction.id),
        (10, "transaction", transaction.id),
    ]:
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    GoalAllocation(
                        workspace_id=workspace.id,
                        goal_id=goal.id,
                        user_id=user.id,
                        amount=Decimal(amount),
                        source=source,
                        transaction_id=transaction_id,
                    )
                )
                await session.flush()
    assert await _pocket_current_amount(session, goal.id) == 75
    await connection.run_sync(_migrate, "downgrade")
    assert not await connection.run_sync(
        lambda conn: inspect(conn).has_table("goal_allocations", schema)
    )
    assert await session.scalar(select(Goal.id).where(Goal.id == goal.id)) == goal.id
    assert (
        await session.scalar(select(Transaction.amount).where(Transaction.id == transaction.id))
        == 200
    )
    await connection.run_sync(_migrate, "upgrade")
    assert await _pocket_current_amount(session, goal.id) == 0
