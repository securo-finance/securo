"""Validation and mutation of transaction-backed pocket allocations.

The transaction remains the only financial fact. These rows only describe
which part of that fact a user earmarked for a pocket.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.goal import Goal, GoalAllocation
from app.models.transaction import Transaction
from app.schemas.goal import GoalAllocationInput
from app.services.dashboard_service import _account_balance_at
from app.services.goal_service import (
    _account_reserved_total,
    _lock_pocket_account,
    _pocket_current_amount,
)


def _transaction_allocations_for_update(
    workspace_id: uuid.UUID, transaction_id: uuid.UUID | set[uuid.UUID]
):
    """Lock allocation rows without locking the nullable joined goal table."""
    return (
        select(GoalAllocation)
        .where(
            GoalAllocation.workspace_id == workspace_id,
            GoalAllocation.transaction_id.in_(
                [transaction_id] if isinstance(transaction_id, uuid.UUID) else transaction_id
            ),
        )
        .with_for_update(of=GoalAllocation)
        .execution_options(populate_existing=True)
    )


async def _lock_allocation_state(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transactions: list[Transaction],
    requested_goal_ids: list[uuid.UUID] | None = None,
) -> tuple[dict[uuid.UUID, Account], list[GoalAllocation], dict[uuid.UUID, Goal]]:
    """Reload locked rows; an ORM identity-map hit alone can retain stale values."""
    transaction_ids = {transaction.id for transaction in transactions}
    allocations_query = select(GoalAllocation).where(
        GoalAllocation.workspace_id == workspace_id,
        GoalAllocation.transaction_id.in_(transaction_ids),
    )
    existing = list(
        (await session.scalars(allocations_query.execution_options(populate_existing=True))).all()
    )
    account_ids = {transaction.account_id for transaction in transactions}
    account_ids.update(row.goal.account_id for row in existing if row.goal.account_id is not None)
    accounts = {}
    for account_id in sorted(account_ids, key=str):
        accounts[account_id] = await _lock_pocket_account(session, workspace_id, account_id)

    result = await session.execute(
        select(Transaction)
        .where(Transaction.id.in_(transaction_ids), Transaction.workspace_id == workspace_id)
        .order_by(Transaction.id)
        .with_for_update(of=Transaction)
        .execution_options(populate_existing=True)
    )
    locked_transactions = list(result.scalars().all())
    if len(locked_transactions) != len(transaction_ids):
        raise ValueError("Transaction not found")
    if any(transaction.account_id not in accounts for transaction in locked_transactions):
        raise ValueError("Transaction account changed; retry this operation")
    existing = list((await session.scalars(allocations_query)).all())
    goal_ids = {row.goal_id for row in existing} | set(requested_goal_ids or [])
    if not goal_ids:
        return {}, [], {}

    goals_result = await session.execute(
        select(Goal)
        .where(Goal.id.in_(goal_ids), Goal.workspace_id == workspace_id)
        .order_by(Goal.id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    goals = {goal.id: goal for goal in goals_result.scalars().all()}
    # A goal may have been deleted while we waited for its account lock.
    existing = list(
        (
            await session.scalars(
                _transaction_allocations_for_update(workspace_id, transaction_ids)
            )
        ).all()
    )
    return accounts, existing, goals


async def _ensure_assignable(
    session: AsyncSession, workspace_id: uuid.UUID, transaction: Transaction, total: Decimal
) -> None:
    if transaction.status != "posted":
        raise ValueError("Only posted transactions can be assigned to pockets")
    category = None
    if transaction.category_id:
        category = await session.scalar(
            select(Category)
            .where(Category.id == transaction.category_id, Category.workspace_id == workspace_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if category is None:
            raise ValueError("Category not found")
    if transaction.is_ignored or (category and category.is_ignored):
        raise ValueError("Ignored transactions cannot be assigned to pockets")
    if transaction.type not in ("debit", "credit"):
        raise ValueError("Transaction type must be debit or credit")
    if total > abs(Decimal(str(transaction.amount))):
        raise ValueError("Pocket assignments exceed the transaction amount")


def _ensure_matching_pocket(transaction: Transaction, account: Account, goal: Goal) -> None:
    if goal.tracking_type != "pocket" or goal.account_id != transaction.account_id:
        raise ValueError("Pocket and transaction must use the same account")
    if goal.currency != transaction.currency or goal.currency != account.currency:
        raise ValueError("Pocket and transaction must use the same currency")


async def _ensure_nonnegative_replacement(
    session: AsyncSession, existing: list[GoalAllocation], signed_amounts: dict[uuid.UUID, Decimal]
) -> None:
    removed: dict[uuid.UUID, Decimal] = {}
    for row in existing:
        removed[row.goal_id] = removed.get(row.goal_id, Decimal("0")) + row.amount
    for goal_id in removed.keys() | signed_amounts.keys():
        current = await _pocket_current_amount(session, goal_id)
        if (
            current - removed.get(goal_id, Decimal("0")) + signed_amounts.get(goal_id, Decimal("0"))
            < 0
        ):
            if signed_amounts.get(goal_id, Decimal("0")) < 0:
                raise ValueError("Pocket withdrawal exceeds its reserved balance")
            raise ValueError(
                "Remove pocket spending assignments before reducing its allocated deposits"
            )


async def validate_transaction_allocations(
    session: AsyncSession, workspace_id: uuid.UUID, transaction: Transaction
) -> None:
    """Retained assignments must fit an edited transaction, even when cash is underfunded."""
    accounts, existing, goals = await _lock_allocation_state(session, workspace_id, [transaction])
    if not existing:
        return
    await _ensure_assignable(
        session, workspace_id, transaction, sum((abs(row.amount) for row in existing), Decimal("0"))
    )
    for row in existing:
        _ensure_matching_pocket(transaction, accounts[transaction.account_id], goals[row.goal_id])
        if (row.amount > 0) != (transaction.type == "credit"):
            raise ValueError("Pocket assignments no longer match the transaction type")


async def clear_transactions_allocations(
    session: AsyncSession, workspace_id: uuid.UUID, transactions: list[Transaction]
) -> None:
    """Remove assignments together, including both legs of a deleted transfer."""
    if not transactions:
        return
    _, existing, _ = await _lock_allocation_state(session, workspace_id, transactions)
    await _ensure_nonnegative_replacement(session, existing, {})
    for row in existing:
        await session.delete(row)
    await session.flush()


async def clear_transaction_allocations(
    session: AsyncSession, workspace_id: uuid.UUID, transaction: Transaction
) -> None:
    await clear_transactions_allocations(session, workspace_id, [transaction])


async def release_for_background_delete(
    session: AsyncSession, workspace_id: uuid.UUID, transaction: Transaction
) -> bool:
    """Clear a row's assignments before a job deletes it, without ever raising.

    Sync and other background jobs cannot show the user an error. Returns False,
    leaving everything untouched, when a pocket's spending depends on the row.
    """
    try:
        async with session.begin_nested():
            await clear_transaction_allocations(session, workspace_id, transaction)
    except ValueError:
        return False
    return True


async def replace_transaction_allocations(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    transaction: Transaction,
    requested: list[GoalAllocationInput],
) -> None:
    """Atomically replace all pocket assignments for one transaction."""
    goal_ids = [item.goal_id for item in requested]
    accounts, existing, goals = await _lock_allocation_state(
        session, workspace_id, [transaction], goal_ids
    )
    if len(goal_ids) != len(set(goal_ids)):
        raise ValueError("A transaction can assign each pocket only once")
    if any(goal_id not in goals for goal_id in goal_ids):
        raise ValueError("Pocket not found")
    if requested and transaction.source == "opening_balance" and (
        accounts[transaction.account_id].connection_id is not None
    ):
        # The provider reconciler rewrites or removes this synthetic row.
        raise ValueError("Synced opening balances cannot be assigned to pockets")

    # Explicit [] remains a cleanup path when a transaction becomes pending,
    # ignored, or otherwise incompatible. Removing spent deposits is rejected.
    total = sum((item.amount for item in requested), Decimal("0"))
    existing_by_goal = {row.goal_id: row.amount for row in existing}
    signed_amounts = {}
    if requested:
        await _ensure_assignable(session, workspace_id, transaction, total)
        account = accounts[transaction.account_id]
        for item in requested:
            goal = goals[item.goal_id]
            _ensure_matching_pocket(transaction, account, goal)
            old_effect = Decimal(str(existing_by_goal.get(item.goal_id, 0)))
            if (
                transaction.type == "credit"
                and goal.status != "active"
                and item.amount > max(old_effect, Decimal("0"))
            ):
                raise ValueError("Only active pockets can receive transaction deposits")
            if (
                transaction.type == "debit"
                and goal.status == "archived"
                and item.amount > abs(min(old_effect, Decimal("0")))
            ):
                raise ValueError("Archived pockets cannot be used by a transaction")
            signed_amounts[item.goal_id] = (
                item.amount if transaction.type == "credit" else -item.amount
            )

        if transaction.type == "credit":
            balance = Decimal(str(await _account_balance_at(session, account, app_today())))
            reserved = await _account_reserved_total(session, workspace_id, account.id)
            existing_on_target = sum(
                (row.amount for row in existing if row.goal.account_id == transaction.account_id),
                Decimal("0"),
            )
            resulting_reserved = reserved - existing_on_target + total
            if resulting_reserved > reserved and resulting_reserved > balance:
                raise ValueError("Pocket assignments exceed the account's available balance")

    await _ensure_nonnegative_replacement(session, existing, signed_amounts)
    for row in existing:
        await session.delete(row)
    await session.flush()
    for goal_id, amount in signed_amounts.items():
        session.add(
            GoalAllocation(
                workspace_id=workspace_id,
                goal_id=goal_id,
                transaction_id=transaction.id,
                user_id=user_id,
                amount=amount,
                source="transaction",
            )
        )
    await session.flush()
