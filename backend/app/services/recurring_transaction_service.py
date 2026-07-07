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

    # A recurring transaction must always have an account — reject an explicit
    # null, and verify ownership of any new account_id.
    if "account_id" in update_data:
        new_account_id = update_data["account_id"]
        if new_account_id is None:
            raise ValueError("account_id is required")
        if new_account_id != recurring.account_id:
            await _verify_account_in_workspace(session, workspace_id, new_account_id)

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
    """Advance a date by the given frequency.

    For monthly/yearly, ``intended_day`` is the day the user actually wants
    (e.g. 31). We cap it to the target month's length so Feb clamps to 28/29,
    but subsequent months recover to 31/30 instead of sticking at 28.
    Falls back to ``current.day`` when not provided."""
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
    """Compute all occurrence dates for a recurring pattern within [range_start, range_end).
    Pure date math — no DB writes. Used by dashboard for virtual projections."""
    day = intended_day if intended_day else start.day
    occurrences: list[date] = []
    current = start
    # Advance to range_start without collecting
    while current < range_start:
        if end_date and current > end_date:
            return occurrences
        current = _advance_date(current, frequency, intended_day=day)
    # Collect occurrences within range
    while current < range_end:
        if end_date and current > end_date:
            break
        occurrences.append(current)
        current = _advance_date(current, frequency, intended_day=day)
        if len(occurrences) > 200:  # safety limit
            break
    return occurrences


async def generate_pending(
    session: AsyncSession, user_id: uuid.UUID, up_to: Optional[date] = None
) -> int:
    """Generate transactions for all pending recurring transactions up to a given date.
    If up_to is None, defaults to today. This allows the dashboard to pre-generate
    transactions for future months when the user navigates ahead.
    Returns the count of transactions generated."""
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
        # Legacy rows may exist with a null account_id from before account_id
        # was required. Skip them rather than crashing on Transaction's NOT NULL
        # constraint — the user should edit the recurring to fix it.
        if recurring.account_id is None:
            continue
        # Generate transactions until next_occurrence is past the cutoff
        while recurring.next_occurrence <= cutoff:
            # Check if past end_date
            if recurring.end_date and recurring.next_occurrence > recurring.end_date:
                recurring.is_active = False
                break

            # If a real transaction (synced/imported/manual) already covers this
            # occurrence, link it to the bill instead of writing a duplicate
            # placeholder (issue #116). Otherwise materialize the placeholder,
            # stamped with the recurring link so a later synced charge merges
            # into it rather than duplicating.
            existing_real = await recurring_match_service.find_real_tx_for_occurrence(
                session, recurring, recurring.next_occurrence
            )
            if existing_real is not None:
                existing_real.recurring_transaction_id = recurring.id
            else:
                transaction = Transaction(
                    user_id=user_id,
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
                account = await session.get(Account, recurring.account_id)
                apply_effective_date(transaction, account)
                session.add(transaction)
                await session.flush()
                await stamp_primary_amount(session, user_id, transaction)
                count += 1

            # Advance to next occurrence
            recurring.next_occurrence = _advance_date(
                recurring.next_occurrence, recurring.frequency,
                intended_day=recurring.day_of_month or recurring.start_date.day,
            )

            # Check again if past end_date after advancing
            if recurring.end_date and recurring.next_occurrence > recurring.end_date:
                recurring.is_active = False

    await session.commit()
    return count
