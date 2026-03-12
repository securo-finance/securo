import uuid
from datetime import date as _Date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import case, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.schemas.account import AccountCreate, AccountUpdate


async def get_accounts(session: AsyncSession, user_id: uuid.UUID, include_closed: bool = False) -> list[dict]:
    # Subquery: compute current_balance per account from transactions in one pass
    signed_amount = case(
        (Transaction.type == "credit", Transaction.amount),
        else_=-Transaction.amount,
    )

    balance_sq = (
        select(
            Transaction.account_id,
            func.coalesce(func.sum(signed_amount), 0).label("current_balance"),
        )
        .group_by(Transaction.account_id)
        .subquery()
    )

    # Subquery: compute previous_balance (balance at end of previous month)
    today = _Date.today()
    first_of_month = today.replace(day=1)
    prev_month_end = first_of_month - timedelta(days=1)

    prev_balance_sq = (
        select(
            Transaction.account_id,
            func.coalesce(func.sum(signed_amount), 0).label("previous_balance"),
        )
        .where(Transaction.date <= prev_month_end)
        .group_by(Transaction.account_id)
        .subquery()
    )

    # Build the query
    query = (
        select(
            Account,
            func.coalesce(balance_sq.c.current_balance, 0).label("current_balance"),
            func.coalesce(prev_balance_sq.c.previous_balance, 0).label("previous_balance"),
        )
        .outerjoin(BankConnection)
        .outerjoin(balance_sq, Account.id == balance_sq.c.account_id)
        .outerjoin(prev_balance_sq, Account.id == prev_balance_sq.c.account_id)
        .where(
            or_(
                Account.user_id == user_id,
                BankConnection.user_id == user_id,
            )
        )
    )
    if not include_closed:
        query = query.where(Account.is_closed == False)
    query = query.order_by(Account.name)
    result = await session.execute(query)

    return [
        {
            "id": acc.id,
            "user_id": acc.user_id,
            "connection_id": acc.connection_id,
            "external_id": acc.external_id,
            "name": acc.name,
            "type": acc.type,
            "balance": acc.balance,
            "currency": acc.currency,
            # Connected CC: provider stores positive for debt → negate.
            # Manual accounts: transaction math already gives correct sign.
            "current_balance": float(acc.balance) * (-1 if acc.type == "credit_card" else 1) if acc.connection_id else float(current_balance or 0),
            "previous_balance": float(previous_balance or 0),
            "is_closed": acc.is_closed,
            "closed_at": acc.closed_at,
        }
        for acc, current_balance, previous_balance in result.all()
    ]


async def get_account(session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID) -> Optional[Account]:
    result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == account_id,
            or_(
                Account.user_id == user_id,
                BankConnection.user_id == user_id,
            ),
        )
    )
    return result.scalar_one_or_none()


async def create_account(session: AsyncSession, user_id: uuid.UUID, data: AccountCreate) -> Account:
    account = Account(
        user_id=user_id,
        name=data.name,
        type=data.type,
        balance=data.balance,
        currency=data.currency,
    )
    session.add(account)
    await session.flush()  # get account.id without committing

    if data.balance > Decimal("0.00"):
        # Credit cards: opening balance represents debt → record as debit.
        # Other accounts: opening balance represents assets → record as credit.
        opening_type = "debit" if data.type == "credit_card" else "credit"
        opening_tx = Transaction(
            user_id=user_id,
            account_id=account.id,
            description="Saldo inicial",
            amount=data.balance,
            currency=data.currency,
            date=data.balance_date or _Date.today(),
            type=opening_type,
            source="opening_balance",
        )
        session.add(opening_tx)

    await session.commit()
    await session.refresh(account)
    return account


async def update_account(
    session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID, data: AccountUpdate
) -> Optional[Account]:
    account = await get_account(session, account_id, user_id)
    if not account:
        return None

    # Only allow editing manual accounts
    if account.connection_id is not None:
        raise ValueError("Cannot edit bank-connected accounts")

    update_data = data.model_dump(exclude_unset=True)
    balance_date = update_data.pop("balance_date", None)

    for key, value in update_data.items():
        setattr(account, key, value)

    # When balance changes, sync the opening_balance transaction
    if "balance" in update_data:
        new_balance = update_data["balance"]
        existing_opening = await session.execute(
            select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.source == "opening_balance",
            )
        )
        opening_tx = existing_opening.scalar_one_or_none()
        opening_type = "debit" if account.type == "credit_card" else "credit"

        if new_balance > Decimal("0.00"):
            if opening_tx:
                opening_tx.amount = new_balance
                opening_tx.type = opening_type
                if balance_date:
                    opening_tx.date = balance_date
            else:
                opening_tx = Transaction(
                    user_id=account.user_id,
                    account_id=account_id,
                    description="Saldo inicial",
                    amount=new_balance,
                    currency=account.currency,
                    date=balance_date or _Date.today(),
                    type=opening_type,
                    source="opening_balance",
                )
                session.add(opening_tx)
        elif opening_tx:
            await session.delete(opening_tx)

    await session.commit()
    await session.refresh(account)
    return account


async def delete_account(session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    account = await get_account(session, account_id, user_id)
    if not account:
        return False

    # Only allow deleting manual accounts
    if account.connection_id is not None:
        raise ValueError("Cannot delete bank-connected accounts")

    await session.delete(account)
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

    # Unlink from bank connection so sync skips it
    if account.connection_id is not None:
        account.connection_id = None

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
    if not date_from:
        date_from = today.replace(day=1)
    if not date_to:
        date_to = today

    # For bank-connected accounts, use the stored balance from the provider
    if account.connection_id:
        current_balance = float(account.balance)
    else:
        # Current balance = SUM(credit amounts) - SUM(debit amounts)
        balance_result = await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (Transaction.type == "credit", Transaction.amount),
                            else_=-Transaction.amount,
                        )
                    ),
                    0,
                )
            ).where(Transaction.account_id == account_id)
        )
        current_balance = float(balance_result.scalar())

    # Connected CC: provider balance is positive for debt → negate.
    # Manual CC: transaction math already gives negative for debt.
    if account.type == "credit_card" and account.connection_id:
        current_balance = -current_balance

    # Income = SUM of credit transactions in [date_from, date_to] (excluding opening_balance and transfers)
    income_result = await session.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.account_id == account_id,
            Transaction.type == "credit",
            Transaction.source != "opening_balance",
            Transaction.transfer_pair_id.is_(None),
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        )
    )
    monthly_income = float(income_result.scalar())

    # Expenses = SUM of debit transactions in [date_from, date_to] (as positive value, excluding transfers)
    expenses_result = await session.execute(
        select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(
            Transaction.account_id == account_id,
            Transaction.type == "debit",
            Transaction.transfer_pair_id.is_(None),
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        )
    )
    monthly_expenses = float(expenses_result.scalar())

    return {
        "account_id": account_id,
        "current_balance": current_balance,
        "monthly_income": monthly_income,
        "monthly_expenses": monthly_expenses,
    }


def _signed_amount_expr():
    """credit → +amount, debit → −amount."""
    return case(
        (Transaction.type == "credit", Transaction.amount),
        else_=-Transaction.amount,
    )


async def _account_balance_at(
    session: AsyncSession, account_id: uuid.UUID, cutoff: _Date
) -> float:
    """Get balance for a single account at a specific date."""
    result = await session.execute(
        select(func.coalesce(func.sum(_signed_amount_expr()), 0))
        .where(
            Transaction.account_id == account_id,
            Transaction.date <= cutoff,
        )
    )
    return float(result.scalar() or 0)


async def _account_daily_balance_series(
    session: AsyncSession, account_id: uuid.UUID,
    date_from: _Date, date_to: _Date,
) -> list[dict]:
    """Build daily balance series for [date_from, date_to] inclusive."""
    # Get balance at end of day before range start
    start_balance = await _account_balance_at(session, account_id, date_from - timedelta(days=1))

    # Get daily deltas within range: group by actual date
    result = await session.execute(
        select(
            Transaction.date,
            func.sum(_signed_amount_expr()),
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
    if not date_from:
        date_from = today.replace(day=1)
    if not date_to:
        date_to = today

    sign = -1.0 if (account.type == "credit_card" and account.connection_id) else 1.0

    series = await _account_daily_balance_series(session, account_id, date_from, date_to)

    if sign != 1.0:
        for point in series:
            point["balance"] = round(point["balance"] * sign, 2)

    return series
