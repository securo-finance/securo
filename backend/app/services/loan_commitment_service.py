"""Commit planned loan actions → budget lines + forecast (recurring/pending txs)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.budget import Budget
from app.models.loan_plan_commitment import LoanPlanCommitment
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services.credit_card_service import apply_effective_date


async def _require_loan(session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID) -> Account:
    result = await session.execute(
        select(Account).where(Account.id == account_id, Account.workspace_id == workspace_id)
    )
    account = result.scalar_one_or_none()
    if not account or account.type != "loan":
        raise ValueError("Loan account not found")
    return account


async def list_commitments(
    session: AsyncSession, workspace_id: uuid.UUID, account_id: Optional[uuid.UUID] = None
) -> list[LoanPlanCommitment]:
    q = select(LoanPlanCommitment).where(LoanPlanCommitment.workspace_id == workspace_id)
    if account_id:
        q = q.where(LoanPlanCommitment.account_id == account_id)
    q = q.order_by(LoanPlanCommitment.start_date.desc())
    result = await session.execute(q)
    return list(result.scalars().all())


async def commit_plan_action(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    kind: str,
    amount: Decimal,
    start_date: date,
    end_date: Optional[date] = None,
    day_of_month: Optional[int] = None,
    funding_account_id: Optional[uuid.UUID] = None,
    category_id: Optional[uuid.UUID] = None,
    notes: Optional[str] = None,
) -> LoanPlanCommitment:
    if kind not in ("one_time_prepayment", "recurring_prepayment"):
        raise ValueError("kind must be one_time_prepayment or recurring_prepayment")
    if amount <= 0:
        raise ValueError("amount must be positive")

    loan = await _require_loan(session, account_id, workspace_id)

    funding_id = funding_account_id
    if funding_id is None:
        # Prefer a checking account in the workspace
        fr = await session.execute(
            select(Account)
            .where(
                Account.workspace_id == workspace_id,
                Account.type == "checking",
                Account.is_closed == False,  # noqa: E712
            )
            .order_by(Account.name)
            .limit(1)
        )
        funding = fr.scalar_one_or_none()
        funding_id = funding.id if funding else None

    month_start = start_date.replace(day=1)
    loan_name = loan.display_name or loan.name
    budget: Optional[Budget] = None
    recurring: Optional[RecurringTransaction] = None
    one_time_tx: Optional[Transaction] = None

    if kind == "recurring_prepayment":
        if funding_id is None:
            raise ValueError("funding_account_id required for recurring commitment")
        dom = day_of_month or start_date.day
        recurring = RecurringTransaction(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            account_id=funding_id,
            category_id=category_id,
            description=f"{loan_name} extra principal (committed)",
            amount=amount,
            currency=loan.currency or "USD",
            type="debit",
            frequency="monthly",
            day_of_month=min(dom, 28),
            start_date=start_date,
            end_date=end_date,
            is_active=True,
            auto_generate=True,
            next_occurrence=start_date,
        )
        session.add(recurring)
        await session.flush()

        # Recurring budget line for the cash outflow
        if category_id is not None:
            existing = await session.execute(
                select(Budget).where(
                    Budget.user_id == user_id,
                    Budget.category_id == category_id,
                    Budget.month == month_start,
                    Budget.is_recurring == True,  # noqa: E712
                )
            )
            budget = existing.scalar_one_or_none()
            if budget:
                budget.amount = (budget.amount or Decimal("0")) + amount
            else:
                budget = Budget(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    workspace_id=workspace_id,
                    category_id=category_id,
                    amount=amount,
                    month=month_start,
                    is_recurring=True,
                )
                session.add(budget)
                await session.flush()

    else:  # one_time
        if funding_id is None:
            raise ValueError("funding_account_id required for one-time commitment")
        # Future pending transaction → appears in dashboard forecast
        funding_acc = await session.get(Account, funding_id)
        one_time_tx = Transaction(
            id=uuid.uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            account_id=funding_id,
            category_id=category_id,
            description=f"{loan_name} planned prepayment",
            amount=amount,
            currency=loan.currency or "USD",
            date=start_date,
            type="debit",
            status="pending",
            source="manual",
            notes=notes or "Committed loan prepayment",
        )
        if funding_acc:
            apply_effective_date(one_time_tx, funding_acc)
        session.add(one_time_tx)
        await session.flush()

        if category_id is not None:
            existing = await session.execute(
                select(Budget).where(
                    Budget.user_id == user_id,
                    Budget.category_id == category_id,
                    Budget.month == month_start,
                    Budget.is_recurring == False,  # noqa: E712
                )
            )
            budget = existing.scalar_one_or_none()
            if budget:
                budget.amount = (budget.amount or Decimal("0")) + amount
            else:
                budget = Budget(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    workspace_id=workspace_id,
                    category_id=category_id,
                    amount=amount,
                    month=month_start,
                    is_recurring=False,
                )
                session.add(budget)
                await session.flush()

    commitment = LoanPlanCommitment(
        id=uuid.uuid4(),
        account_id=account_id,
        workspace_id=workspace_id,
        user_id=user_id,
        kind=kind,
        amount=amount,
        start_date=start_date,
        end_date=end_date,
        day_of_month=day_of_month or start_date.day,
        status="active",
        funding_account_id=funding_id,
        category_id=category_id,
        budget_id=budget.id if budget else None,
        recurring_transaction_id=recurring.id if recurring else None,
        transaction_id=one_time_tx.id if one_time_tx else None,
        notes=notes,
    )
    session.add(commitment)
    await session.commit()
    await session.refresh(commitment)
    return commitment


async def cancel_commitment(
    session: AsyncSession, commitment_id: uuid.UUID, workspace_id: uuid.UUID
) -> LoanPlanCommitment:
    result = await session.execute(
        select(LoanPlanCommitment).where(
            LoanPlanCommitment.id == commitment_id,
            LoanPlanCommitment.workspace_id == workspace_id,
        )
    )
    commitment = result.scalar_one_or_none()
    if not commitment:
        raise ValueError("Commitment not found")
    commitment.status = "cancelled"
    commitment.updated_at = datetime.now(timezone.utc)

    if commitment.recurring_transaction_id:
        rec = await session.get(RecurringTransaction, commitment.recurring_transaction_id)
        if rec:
            rec.is_active = False
    if commitment.transaction_id:
        tx = await session.get(Transaction, commitment.transaction_id)
        if tx and tx.status == "pending":
            tx.is_ignored = True

    await session.commit()
    await session.refresh(commitment)
    return commitment
