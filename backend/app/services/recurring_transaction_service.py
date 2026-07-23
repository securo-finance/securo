import calendar
import uuid
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate, RecurringTransactionUpdate
from app.services import recurring_match_service
from app.services.credit_card_service import apply_effective_date
from app.services.fx_rate_service import stamp_primary_amount


async def _verify_account_in_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, account_id: uuid.UUID
) -> None:
    """Raise ValueError if the account isn't reachable from this workspace."""
    result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == account_id,
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
            ),
        )
    )
    if result.scalar_one_or_none() is None:
        raise ValueError("Account not found")


async def get_recurring_transactions(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[RecurringTransaction]:
    result = await session.execute(
        select(RecurringTransaction)
        .where(RecurringTransaction.workspace_id == workspace_id)
        .order_by(RecurringTransaction.next_occurrence)
    )
    return list(result.scalars().all())


async def get_recurring_transaction(
    session: AsyncSession, recurring_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[RecurringTransaction]:
    result = await session.execute(
        select(RecurringTransaction)
        .where(RecurringTransaction.id == recurring_id, RecurringTransaction.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def create_recurring_transaction(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: RecurringTransactionCreate,
) -> RecurringTransaction:
    await _verify_account_in_workspace(session, workspace_id, data.account_id)
    if data.target_account_id:
        await _verify_account_in_workspace(session, workspace_id, data.target_account_id)
    next_occ = data.start_date
    if data.skip_first:
        next_occ = _advance_date(
            data.start_date, data.frequency,
            intended_day=data.day_of_month or data.start_date.day,
        )
    recurring = RecurringTransaction(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=data.account_id,
        target_account_id=data.target_account_id,
        category_id=data.category_id,
        description=data.description,
        amount=data.amount,
        currency=data.currency,
        type=data.type,
        frequency=data.frequency,
        day_of_month=data.day_of_month,
        start_date=data.start_date,
        end_date=data.end_date,
        auto_generate=data.auto_generate,
        next_occurrence=next_occ,
    )
    session.add(recurring)
    await session.flush()
    await stamp_primary_amount(
        session, user_id, recurring,
        date_field="start_date",
    )
    await session.commit()
    await session.refresh(recurring)
    return recurring


async def update_recurring_transaction(
    session: AsyncSession, recurring_id: uuid.UUID, workspace_id: uuid.UUID, data: RecurringTransactionUpdate
) -> Optional[RecurringTransaction]:
    recurring = await get_recurring_transaction(session, recurring_id, workspace_id)
    if not recurring:
        return None

    update_data = data.model_dump(exclude_unset=True)

    if "account_id" in update_data:
        new_account_id = update_data["account_id"]
        if new_account_id is None:
            raise ValueError("account_id is required")
        if new_account_id != recurring.account_id:
            await _verify_account_in_workspace(session, workspace_id, new_account_id)

    if "target_account_id" in update_data and update_data["target_account_id"]:
        await _verify_account_in_workspace(session, workspace_id, update_data["target_account_id"])

    for key, value in update_data.items():
        setattr(recurring, key, value)

    await session.commit()
    await session.refresh(recurring)
    return recurring


async def delete_recurring_transaction(
    session: AsyncSession, recurring_id: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    recurring = await get_recurring_transaction(session, recurring_id, workspace_id)
    if not recurring:
        return False

    await session.delete(recurring)
    await session.commit()
    return True


def _advance_date(
    current: date, frequency: str, intended_day: Optional[int] = None,
) -> date:
    """Advance a date by the given frequency."""
    if frequency == "weekly":
        return current + timedelta(weeks=1)
    target_day = intended_day if intended_day else current.day
    if frequency == "yearly":
        year = current.year + 1
        day = min(target_day, calendar.monthrange(year, current.month)[1])
        return date(year, current.month, day)
    # monthly (default)
    month = current.month + 1
    year = current.year
    if month > 12:
        month = 1
        year += 1
    day = min(target_day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def get_occurrences_in_range(
    start: date, frequency: str, end_date: Optional[date],
    range_start: date, range_end: date,
    intended_day: Optional[int] = None,
) -> list[date]:
    """Compute all occurrence dates for a recurring pattern within [range_start, range_end)."""
    day = intended_day if intended_day else start.day
    occurrences: list[date] = []
    current = start
    while current < range_start:
        if end_date and current > end_date:
            return occurrences
        current = _advance_date(current, frequency, intended_day=day)
    while current < range_end:
        if end_date and current > end_date:
            break
        occurrences.append(current)
        current = _advance_date(current, frequency, intended_day=day)
        if len(occurrences) > 200:
            break
    return occurrences


async def generate_pending(
    session: AsyncSession, user_id: uuid.UUID, up_to: Optional[date] = None
) -> int:
    """Generate transactions for all pending recurring transactions up to a given date."""
    cutoff = up_to or date.today()

    result = await session.execute(
        select(RecurringTransaction)
        .where(
            RecurringTransaction.user_id == user_id,
            RecurringTransaction.is_active == True,
            RecurringTransaction.auto_generate == True,
            RecurringTransaction.next_occurrence <= cutoff,
        )
    )
    recurring_list = list(result.scalars().all())

    count = 0
    for recurring in recurring_list:
        if recurring.account_id is None:
            continue

        while recurring.next_occurrence <= cutoff:
            if recurring.end_date and recurring.next_occurrence > recurring.end_date:
                recurring.is_active = False
                break

            existing_real = await recurring_match_service.find_real_tx_for_occurrence(
                session, recurring, recurring.next_occurrence
            )
            if existing_real is not None:
                existing_real.recurring_transaction_id = recurring.id
            else:
                account = await session.get(Account, recurring.account_id)
                target_acc = await session.get(Account, recurring.target_account_id) if recurring.target_account_id else None

                is_loan_repayment = (
                    recurring.type == "repayment" or
                    (target_acc and target_acc.type == "loan") or
                    (account and account.type == "loan")
                )

                if is_loan_repayment:
                    loan_acc = target_acc if target_acc and target_acc.type == "loan" else (account if account and account.type == "loan" else None)
                    funding_acc = account if account and account.id == recurring.account_id else (target_acc if target_acc and target_acc.id == recurring.account_id else None)

                    if loan_acc and loan_acc.balance <= Decimal("0.00"):
                        # Loan debt is 0 or paid off: deactivate recurring EMI payments
                        recurring.is_active = False
                        session.add(recurring)
                        continue

                    funding_acc_id = recurring.account_id or (funding_acc.id if funding_acc else (loan_acc.id if loan_acc else None))
                    tx_debit = Transaction(
                        user_id=user_id,
                        workspace_id=recurring.workspace_id,
                        account_id=funding_acc_id,
                        category_id=recurring.category_id,
                        description=recurring.description,
                        amount=recurring.amount,
                        currency=recurring.currency,
                        date=recurring.next_occurrence,
                        type="debit",
                        source="loan_repayment",
                        recurring_transaction_id=recurring.id,
                    )
                    if funding_acc:
                        apply_effective_date(tx_debit, funding_acc)
                    session.add(tx_debit)
                    await session.flush()
                    await stamp_primary_amount(session, user_id, tx_debit)

                    if loan_acc:
                        loan_acc.balance = max(Decimal("0.00"), loan_acc.balance - recurring.amount)
                        session.add(loan_acc)

                    count += 1
                else:
                    # Standard debit/credit transaction
                    transaction = Transaction(
                        user_id=user_id,
                        workspace_id=recurring.workspace_id,
                        account_id=recurring.account_id,
                        category_id=recurring.category_id,
                        description=recurring.description,
                        amount=recurring.amount,
                        currency=recurring.currency,
                        date=recurring.next_occurrence,
                        type=recurring.type,
                        source="recurring",
                        recurring_transaction_id=recurring.id,
                    )
                    if account:
                        apply_effective_date(transaction, account)
                    session.add(transaction)
                    await session.flush()
                    await stamp_primary_amount(session, user_id, transaction)
                    count += 1

            recurring.next_occurrence = _advance_date(
                recurring.next_occurrence, recurring.frequency,
                intended_day=recurring.day_of_month or recurring.start_date.day,
            )

            if recurring.end_date and recurring.next_occurrence > recurring.end_date:
                recurring.is_active = False

    await session.commit()
    return count
