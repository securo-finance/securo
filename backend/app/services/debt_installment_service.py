import uuid
from datetime import date as _date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.debt import Debt, DebtInstallment, DebtPlan
from app.models.transaction import Transaction
from app.schemas.debt import DebtInstallmentPay


async def _get_installment_with_plan(
    session: AsyncSession, installment_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[DebtInstallment]:
    result = await session.execute(
        select(DebtInstallment)
        .where(DebtInstallment.id == installment_id, DebtInstallment.workspace_id == workspace_id)
        .options(selectinload(DebtInstallment.plan))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _validate_transaction(
    session: AsyncSession, transaction_id: Optional[uuid.UUID], workspace_id: uuid.UUID
) -> None:
    if transaction_id is None:
        return
    result = await session.execute(
        select(Transaction).where(
            Transaction.id == transaction_id, Transaction.workspace_id == workspace_id
        )
    )
    if result.scalar_one_or_none() is None:
        raise ValueError("Linked transaction not found")


async def _apply_principal_paydown(session: AsyncSession, debt_id: uuid.UUID, principal_paid) -> None:
    debt = await session.get(Debt, debt_id)
    if debt is None:
        return
    debt.current_balance = max(debt.current_balance - principal_paid, type(debt.current_balance)("0"))
    if debt.current_balance == 0:
        debt.status = "paid_off"


async def mark_installment_paid(
    session: AsyncSession,
    installment_id: uuid.UUID,
    workspace_id: uuid.UUID,
    data: DebtInstallmentPay,
) -> Optional[DebtInstallment]:
    installment = await _get_installment_with_plan(session, installment_id, workspace_id)
    if not installment:
        return None
    if installment.status == "paid":
        raise ValueError("Installment is already paid")

    if installment.plan.collection_mode == "payroll_deduction" and data.transaction_id is not None:
        # The deduction happens on the payroll before the salary is
        # deposited — it never appears as a real bank transaction, so
        # there is nothing to link here.
        raise ValueError(
            "Payroll-deducted installments cannot be linked to a transaction"
        )

    await _validate_transaction(session, data.transaction_id, workspace_id)

    installment.status = "paid"
    installment.paid_date = data.paid_date
    installment.paid_amount = data.paid_amount or installment.amount
    installment.transaction_id = data.transaction_id

    await _apply_principal_paydown(session, installment.plan.debt_id, installment.principal_portion)

    await session.commit()
    await session.refresh(installment)
    return installment


async def auto_settle_payroll_installments(session: AsyncSession, today: _date) -> int:
    """Mark payroll-deducted installments paid once their due date has
    passed. The deduction is contractually guaranteed by the employer,
    so it doesn't need a triggering event (e.g. matching a salary
    deposit) — it advances by date alone, same as a recurring bill that
    never needs a placeholder transaction.
    """
    result = await session.execute(
        select(DebtInstallment)
        .join(DebtPlan, DebtInstallment.plan_id == DebtPlan.id)
        .where(
            DebtPlan.collection_mode == "payroll_deduction",
            DebtPlan.status == "active",
            DebtInstallment.status == "pending",
            DebtInstallment.due_date <= today,
        )
        .options(selectinload(DebtInstallment.plan))
        .execution_options(populate_existing=True)
    )
    installments = list(result.scalars().all())
    for installment in installments:
        installment.status = "paid"
        installment.paid_date = installment.due_date
        installment.paid_amount = installment.amount
        await _apply_principal_paydown(session, installment.plan.debt_id, installment.principal_portion)
    if installments:
        await session.commit()
    return len(installments)
