import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.models.account import Account
from app.models.category import Category
from app.models.mortgage_payment_allocation import (
    MORTGAGE_PRINCIPAL_SOURCE,
    MortgagePaymentAllocation,
)
from app.models.transaction import Transaction
from app.services import mortgage_service

router = APIRouter(prefix="/api/mortgage-payments", tags=["mortgage payments"])


class AllocationInput(BaseModel):
    loan_account_id: uuid.UUID
    principal_amount: Decimal = Field(ge=0, decimal_places=2)
    interest_amount: Decimal = Field(ge=0, decimal_places=2)


class AllocationReplace(BaseModel):
    allocations: list[AllocationInput] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_loan_parts(self):
        ids = [row.loan_account_id for row in self.allocations]
        if len(ids) != len(set(ids)):
            raise ValueError("Each loan part can only appear once")
        return self


def _percentage(part: Decimal, total: Decimal) -> float:
    return float(part / total * 100) if total else 0.0


def _allocation_read(row: MortgagePaymentAllocation, loan: Account) -> dict:
    total = row.principal_amount + row.interest_amount
    bank_managed = loan.connection_id is not None
    return {
        "loan_account_id": row.loan_account_id,
        "loan_name": loan.display_name or loan.name,
        "principal_amount": float(row.principal_amount),
        "interest_amount": float(row.interest_amount),
        "payment_amount": float(total),
        "principal_percentage": _percentage(row.principal_amount, total),
        "interest_percentage": _percentage(row.interest_amount, total),
        # The bank reports a connected loan's balance itself, so its principal
        # is never posted here; that is not the same as a pre-snapshot payment.
        "bank_managed": bank_managed,
        "historical": (
            not bank_managed and row.principal_amount > 0 and row.loan_transaction_id is None
        ),
    }


def _payment_read(
    payment: Transaction,
    payment_account: Account,
    rows: list[tuple[MortgagePaymentAllocation, Account]],
) -> dict:
    principal_total = sum((row.principal_amount for row, _ in rows), Decimal("0.00"))
    interest_total = sum((row.interest_amount for row, _ in rows), Decimal("0.00"))
    return {
        "payment_transaction_id": payment.id,
        "payment_amount": float(payment.amount),
        "currency": payment.currency,
        "payment_account_name": payment_account.display_name or payment_account.name,
        "allocations": [_allocation_read(row, loan) for row, loan in rows],
        "principal_total": float(principal_total),
        "interest_total": float(interest_total),
        "principal_percentage": _percentage(principal_total, payment.amount),
        "interest_percentage": _percentage(interest_total, payment.amount),
    }


async def _get_payment(
    session: AsyncSession,
    payment_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    lock: bool = False,
):
    query = (
        select(Transaction, Account)
        .join(Account, Transaction.account_id == Account.id)
        .where(Transaction.id == payment_id, Transaction.workspace_id == workspace_id)
    )
    if lock:
        # Replacing a breakdown deletes and re-inserts its rows; two saves of
        # the same payment must take turns, or both insert the same loan part.
        query = query.with_for_update(of=Transaction)
    result = await session.execute(query)
    return result.one_or_none()


async def _delete_allocations(
    session: AsyncSession, payment_id: uuid.UUID, workspace_id: uuid.UUID
) -> None:
    existing = await session.execute(
        select(MortgagePaymentAllocation.loan_transaction_id).where(
            MortgagePaymentAllocation.payment_transaction_id == payment_id,
            MortgagePaymentAllocation.workspace_id == workspace_id,
        )
    )
    loan_transaction_ids = [row[0] for row in existing.all() if row[0] is not None]
    await session.execute(
        delete(MortgagePaymentAllocation).where(
            MortgagePaymentAllocation.payment_transaction_id == payment_id,
            MortgagePaymentAllocation.workspace_id == workspace_id,
        )
    )
    if loan_transaction_ids:
        await session.execute(
            delete(Transaction).where(
                Transaction.id.in_(loan_transaction_ids),
                Transaction.source == MORTGAGE_PRINCIPAL_SOURCE,
            )
        )
    await session.flush()


@router.get("/{payment_id}")
async def get_mortgage_payment_allocations(
    payment_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    payment_row = await _get_payment(session, payment_id, ctx.workspace.id)
    if payment_row is None:
        raise HTTPException(404, "Payment transaction not found")
    payment, payment_account = payment_row
    result = await session.execute(
        select(MortgagePaymentAllocation, Account)
        .join(Account, MortgagePaymentAllocation.loan_account_id == Account.id)
        .where(
            MortgagePaymentAllocation.payment_transaction_id == payment_id,
            MortgagePaymentAllocation.workspace_id == ctx.workspace.id,
        )
        .order_by(Account.name)
    )
    rows = [(allocation, loan) for allocation, loan in result.all()]
    return _payment_read(payment, payment_account, rows)


@router.put("/{payment_id}")
async def replace_mortgage_payment_allocations(
    payment_id: uuid.UUID,
    data: AllocationReplace,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    payment_row = await _get_payment(session, payment_id, ctx.workspace.id, lock=True)
    if payment_row is None:
        raise HTTPException(404, "Payment transaction not found")
    payment, payment_account = payment_row
    if payment.type != "debit" or payment_account.type == "loan" or payment.amount <= 0:
        raise HTTPException(400, "Mortgage allocations require a debit from a non-loan account")
    if payment.status != "posted":
        raise HTTPException(400, "Mortgage allocations require a posted payment")
    if payment.is_ignored:
        raise HTTPException(400, "An ignored payment cannot have a mortgage breakdown")
    if payment.category_id is not None and await session.scalar(
        select(Category.is_ignored).where(Category.id == payment.category_id)
    ):
        raise HTTPException(400, "A payment in an ignored category cannot have a mortgage breakdown")
    if payment.transfer_pair_id is not None:
        raise HTTPException(400, "A transfer to another account cannot be a mortgage payment")
    total = sum(
        (row.principal_amount + row.interest_amount for row in data.allocations),
        Decimal("0.00"),
    )
    if total != payment.amount:
        raise HTTPException(400, "Principal and interest must add up to the payment amount")

    ids = [row.loan_account_id for row in data.allocations]
    result = await session.execute(
        select(Account).where(
            Account.id.in_(ids),
            Account.workspace_id == ctx.workspace.id,
            Account.type == "loan",
        )
    )
    loans = {account.id: account for account in result.scalars().all()}
    if len(loans) != len(ids):
        raise HTTPException(400, "Every linked account must be a loan in this workspace")
    if any(account.secured_asset_id is None for account in loans.values()):
        raise HTTPException(400, "Every loan part must be linked to a property")
    if any(account.currency != payment.currency for account in loans.values()):
        raise HTTPException(400, "Payment and loan parts must use the same currency")

    await _delete_allocations(session, payment_id, ctx.workspace.id)

    allocation_rows: list[tuple[MortgagePaymentAllocation, Account]] = []
    for item in data.allocations:
        loan = loans[item.loan_account_id]
        loan_tx_id = None
        opening_date = await mortgage_service.opening_balance_date(session, loan)
        if mortgage_service.posts_principal(
            loan, item.principal_amount, payment.date, opening_date
        ):
            loan_tx = mortgage_service.principal_entry(
                loan, ctx.user_id, payment, item.principal_amount
            )
            session.add(loan_tx)
            await session.flush()
            loan_tx_id = loan_tx.id
        allocation = MortgagePaymentAllocation(
            workspace_id=ctx.workspace.id,
            payment_transaction_id=payment_id,
            loan_account_id=loan.id,
            principal_amount=item.principal_amount,
            interest_amount=item.interest_amount,
            loan_transaction_id=loan_tx_id,
        )
        session.add(allocation)
        allocation_rows.append((allocation, loan))

    await session.commit()
    return _payment_read(payment, payment_account, allocation_rows)


@router.delete("/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mortgage_payment_allocations(
    payment_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if await _get_payment(session, payment_id, ctx.workspace.id, lock=True) is None:
        raise HTTPException(404, "Payment transaction not found")
    await _delete_allocations(session, payment_id, ctx.workspace.id)
    await session.commit()
