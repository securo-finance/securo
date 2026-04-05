import uuid
from datetime import date as _Date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import case, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.account import AccountCreate, AccountUpdate
from app.services.month_service import (
    get_current_month_period,
    get_selected_view_period,
    get_monthly_period,
    month_to_period_value,
    period_to_month_start,
    resolve_current_monthly_period,
    resolve_selected_view_monthly_period,
)


async def get_accounts(session: AsyncSession, user_id: uuid.UUID, include_closed: bool = False) -> list[dict]:
    query = (
        select(Account)
        .outerjoin(BankConnection)
        .where(
            or_(
                Account.user_id == user_id,
                BankConnection.user_id == user_id,
            )
        )
        .where(Account.connection_id.is_not(None))
    )
    if not include_closed:
        query = query.where(Account.is_closed == False)
    query = query.order_by(Account.name)
    result = await session.execute(query)

    return [
        {
            "id": acc.id,
            "user_id": acc.user_id,
            "monthly_period_id": acc.monthly_period_id,
            "connection_id": acc.connection_id,
            "external_id": acc.external_id,
            "name": acc.custom_name or acc.name,
            "type": acc.type,
            "currency": acc.currency,
            "bill_import_enabled": acc.bill_import_enabled,
            "is_closed": acc.is_closed,
            "closed_at": acc.closed_at,
        }
        for acc in result.scalars().all()
    ]


async def get_account(session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID) -> Optional[Account]:
    result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == account_id,
            Account.connection_id.is_not(None),
            or_(
                Account.user_id == user_id,
                BankConnection.user_id == user_id,
            ),
        )
    )
    return result.scalar_one_or_none()


async def create_account(session: AsyncSession, user_id: uuid.UUID, data: AccountCreate) -> Account:
    raise ValueError("Manual account creation is no longer supported")


async def update_account(
    session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID, data: AccountUpdate
) -> Optional[Account]:
    account = await get_account(session, account_id, user_id)
    if not account:
        return None

    update_data = data.model_dump(exclude_unset=True)
    if "name" in update_data:
        account.custom_name = update_data["name"]
    if "bill_import_enabled" in update_data:
        account.bill_import_enabled = update_data["bill_import_enabled"]

    await session.commit()
    await session.refresh(account)
    return account


async def delete_account(session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    account = await get_account(session, account_id, user_id)
    if not account:
        return False

    account.is_closed = True
    account.closed_at = datetime.now(timezone.utc)
    await session.commit()
    return True


async def close_account(
    session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[Account]:
    account = await get_account(session, account_id, user_id)
    if not account:
        return None
    if account.is_closed:
        raise ValueError("Account is already closed")

    account.is_closed = True
    account.closed_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(account)
    return account


async def reopen_account(
    session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[Account]:
    account = await get_account(session, account_id, user_id)
    if not account:
        return None
    if not account.is_closed:
        raise ValueError("Account is not closed")

    account.is_closed = False
    account.closed_at = None

    await session.commit()
    await session.refresh(account)
    return account


async def get_account_summary(
    session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID,
    date_from: Optional[_Date] = None, date_to: Optional[_Date] = None,
) -> Optional[dict]:
    account = await get_account(session, account_id, user_id)
    if not account:
        return None

    today = _Date.today()
    user = await session.get(User, user_id)
    current_period = get_selected_view_period(user) if user else None
    current_monthly_period = await resolve_selected_view_monthly_period(session, user_id, user) if current_period else None
    if not date_from:
        date_from = period_to_month_start(current_period) if current_period else today.replace(day=1)
    if not date_to:
        date_to = today

    # Use amount_primary only when tx currency differs from account currency
    effective_amount = case(
        (Transaction.currency == account.currency, Transaction.amount),
        else_=func.coalesce(Transaction.amount_primary, Transaction.amount),
    )

    # For bank-connected accounts, use the stored balance from the provider
    if account.connection_id:
        current_balance = float(account.balance)
    else:
        # Current balance = SUM(credit amounts) - SUM(debit amounts)
        balance_filters = [Transaction.account_id == account_id]
        if current_monthly_period is not None and date_from == period_to_month_start(current_period) and date_to == today:
            balance_filters.append(Transaction.monthly_period_id == current_monthly_period.id)
        balance_result = await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (Transaction.type == "credit", effective_amount),
                            else_=-effective_amount,
                        )
                    ),
                    0,
                )
            ).where(*balance_filters)
        )
        current_balance = float(balance_result.scalar())

    # Connected CC: provider balance is positive for debt → negate.
    # Manual CC: transaction math already gives negative for debt.
    if account.type == "credit_card" and account.connection_id:
        current_balance = -current_balance

    # Income = SUM of credit transactions in [date_from, date_to] (excluding opening_balance and transfers)
    income_filters = [
        Transaction.account_id == account_id,
        Transaction.type == "credit",
        Transaction.source != "opening_balance",
        Transaction.transfer_pair_id.is_(None),
    ]
    expense_filters = [
        Transaction.account_id == account_id,
        Transaction.type == "debit",
        Transaction.transfer_pair_id.is_(None),
    ]
    if current_monthly_period is not None and date_from == period_to_month_start(current_period) and date_to == today:
        income_filters.append(Transaction.monthly_period_id == current_monthly_period.id)
        expense_filters.append(Transaction.monthly_period_id == current_monthly_period.id)
    else:
        income_filters.extend([Transaction.date >= date_from, Transaction.date <= date_to])
        expense_filters.extend([Transaction.date >= date_from, Transaction.date <= date_to])

    income_result = await session.execute(
        select(func.coalesce(func.sum(effective_amount), 0)).where(*income_filters)
    )
    monthly_income = float(income_result.scalar())

    # Expenses = SUM of debit transactions in [date_from, date_to] (as positive value, excluding transfers)
    expenses_result = await session.execute(
        select(func.coalesce(func.sum(func.abs(effective_amount)), 0)).where(*expense_filters)
    )
    monthly_expenses = float(expenses_result.scalar())

    return {
        "account_id": account_id,
        "current_balance": current_balance,
        "monthly_income": monthly_income,
        "monthly_expenses": monthly_expenses,
    }


def _signed_amount_expr(account_currency: str):
    """credit → +amount, debit → −amount.
    Uses amount_primary only when tx currency differs from account currency."""
    effective = case(
        (Transaction.currency == account_currency, Transaction.amount),
        else_=func.coalesce(Transaction.amount_primary, Transaction.amount),
    )
    return case(
        (Transaction.type == "credit", effective),
        else_=-effective,
    )


async def _account_balance_at(
    session: AsyncSession, account_id: uuid.UUID, cutoff: _Date,
    account_currency: str = "",
) -> float:
    """Get balance for a single account at a specific date."""
    result = await session.execute(
        select(func.coalesce(func.sum(_signed_amount_expr(account_currency)), 0))
        .where(
            Transaction.account_id == account_id,
            Transaction.date <= cutoff,
        )
    )
    return float(result.scalar() or 0)


async def _account_daily_balance_series(
    session: AsyncSession, account_id: uuid.UUID,
    date_from: _Date, date_to: _Date,
    account_currency: str = "",
) -> list[dict]:
    """Build daily balance series for [date_from, date_to] inclusive."""
    # Get balance at end of day before range start
    start_balance = await _account_balance_at(session, account_id, date_from - timedelta(days=1), account_currency)

    # Get daily deltas within range: group by actual date
    result = await session.execute(
        select(
            Transaction.date,
            func.sum(_signed_amount_expr(account_currency)),
        )
        .where(
            Transaction.account_id == account_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        )
        .group_by(Transaction.date)
    )
    deltas = {row[0]: float(row[1] or 0) for row in result.all()}

    # Build daily series
    series = []
    balance = start_balance
    current = date_from
    while current <= date_to:
        balance += deltas.get(current, 0)
        series.append({"date": current.isoformat(), "balance": round(balance, 2)})
        current += timedelta(days=1)

    return series


async def get_account_balance_history(
    session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID,
    date_from: Optional[_Date] = None, date_to: Optional[_Date] = None,
) -> Optional[list[dict]]:
    account = await get_account(session, account_id, user_id)
    if not account:
        return None

    today = _Date.today()
    if not date_from or not date_to:
        user = await session.get(User, user_id)
        selected_period = get_selected_view_period(user) if user else None
        if not date_from:
            date_from = period_to_month_start(selected_period) if selected_period else today.replace(day=1)
        if not date_to:
            if selected_period and selected_period != get_current_month_period(user) if user else None:
                next_period_start = period_to_month_start(month_to_period_value(date_from.replace(day=28) + timedelta(days=4)))
                date_to = next_period_start - timedelta(days=1)
            else:
                date_to = today

    sign = -1.0 if (account.type == "credit_card" and account.connection_id) else 1.0

    series = await _account_daily_balance_series(session, account_id, date_from, date_to, account.currency)

    if sign != 1.0:
        for point in series:
            point["balance"] = round(point["balance"] * sign, 2)

    return series
