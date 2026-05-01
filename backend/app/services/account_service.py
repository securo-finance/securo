import uuid
from datetime import date as _Date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import case, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.credit_card_bill import CreditCardBill
from app.models.transaction import Transaction
from app.schemas.account import AccountCreate, AccountUpdate
from app.services._query_filters import counts_as_pnl
from app.services.credit_card_service import apply_effective_date, compute_available_credit, get_cycle_dates


def get_account_name(account: Account) -> str:
    return account.display_name or account.name


async def get_accounts(session: AsyncSession, user_id: uuid.UUID, include_closed: bool = False) -> list[dict]:
    # Subquery: compute current_balance per account from transactions in one pass
    # Use amount_primary only when tx currency differs from account currency
    # (converts foreign txs to account's reporting currency)
    effective_amount = case(
        (Transaction.currency == Account.currency, Transaction.amount),
        else_=func.coalesce(Transaction.amount_primary, Transaction.amount),
    )
    signed_amount = case(
        (Transaction.type == "credit", effective_amount),
        else_=-effective_amount,
    )

    balance_sq = (
        select(
            Transaction.account_id,
            func.coalesce(func.sum(signed_amount), 0).label("current_balance"),
        )
        .join(Account, Transaction.account_id == Account.id)
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
        .join(Account, Transaction.account_id == Account.id)
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
        serialize_account(acc, current_balance, previous_balance)
        for acc, current_balance, previous_balance in result.all()
    ]


def serialize_account(
    acc: Account,
    current_balance: Optional[Decimal],
    previous_balance: Optional[Decimal],
) -> dict:
    # Connected CC: provider stores positive for debt → negate.
    # Manual accounts: transaction math already gives correct sign.
    if acc.connection_id:
        resolved_balance = float(acc.balance) * (-1 if acc.type == "credit_card" else 1)
    else:
        resolved_balance = float(current_balance or 0)

    payload = {
        "id": acc.id,
        "user_id": acc.user_id,
        "connection_id": acc.connection_id,
        "external_id": acc.external_id,
        "name": acc.name,
        "display_name": acc.display_name,
        "type": acc.type,
        "balance": acc.balance,
        "currency": acc.currency,
        "current_balance": resolved_balance,
        "previous_balance": float(previous_balance or 0),
        "is_closed": acc.is_closed,
        "closed_at": acc.closed_at,
        "credit_limit": float(acc.credit_limit) if acc.credit_limit is not None else None,
        "statement_close_day": acc.statement_close_day,
        "payment_due_day": acc.payment_due_day,
        "minimum_payment": float(acc.minimum_payment) if acc.minimum_payment is not None else None,
        "card_brand": acc.card_brand,
        "card_level": acc.card_level,
        "available_credit": None,
        "next_close_date": None,
        "next_due_date": None,
    }

    if acc.type == "credit_card":
        available = compute_available_credit(acc.credit_limit, Decimal(str(resolved_balance)))
        payload["available_credit"] = float(available) if available is not None else None
        cycle = get_cycle_dates(acc.statement_close_day, acc.payment_due_day)
        payload["next_close_date"] = cycle["next_close_date"]
        payload["next_due_date"] = cycle["next_due_date"]

    return payload


async def get_credit_card_bills(
    session: AsyncSession,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    limit: int = 24,
) -> Optional[list[CreditCardBill]]:
    """Return bills for a CC account, newest due_date first.

    Returns None when the account doesn't exist or isn't owned by the user
    (the caller maps that to a 404). Returns [] for non-CC accounts and CC
    accounts with no synced bills — the read path then keeps using the
    cycle-math fallback.
    """
    account = await get_account(session, account_id, user_id)
    if account is None:
        return None
    if account.type != "credit_card":
        return []
    result = await session.execute(
        select(CreditCardBill)
        .where(CreditCardBill.account_id == account_id)
        .order_by(CreditCardBill.due_date.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


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
    is_cc = data.type == "credit_card"
    account = Account(
        user_id=user_id,
        name=data.name,
        type=data.type,
        balance=data.balance,
        currency=data.currency,
        credit_limit=data.credit_limit if is_cc else None,
        statement_close_day=data.statement_close_day if is_cc else None,
        payment_due_day=data.payment_due_day if is_cc else None,
        minimum_payment=data.minimum_payment if is_cc else None,
        card_brand=data.card_brand if is_cc else None,
        card_level=data.card_level if is_cc else None,
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
        apply_effective_date(opening_tx, account)
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

    update_data = data.model_dump(exclude_unset=True)
    balance_date = update_data.pop("balance_date", None)

    # Track whether we need to recompute effective_date for all transactions.
    # Changes to the CC cycle days shift which bill each historical purchase
    # belongs to, so stored effective_dates need to be rebuilt.
    cycle_fields_changed = any(
        k in update_data for k in ("statement_close_day", "payment_due_day")
    )

    # Bank-connected accounts are managed by the sync pipeline. Only credit card
    # metadata (limit + cycle days) can be user-edited, since providers often don't
    # expose those — users fill them in to unlock cycle-aware filtering.
    if account.connection_id is not None:
        editable_fields = {
            "display_name",
            "credit_limit",
            "statement_close_day",
            "payment_due_day",
            "minimum_payment",
            "card_brand",
            "card_level",
        }
        disallowed = set(update_data.keys()) - editable_fields
        if disallowed:
            raise ValueError("Cannot edit bank-connected accounts")
        cc_fields = editable_fields - {"display_name"}
        cc_update = {k: v for k, v in update_data.items() if k in cc_fields}
        if cc_update and account.type != "credit_card":
            raise ValueError("Credit card fields can only be set on credit card accounts")
        for key, value in update_data.items():
            setattr(account, key, value)
        if cycle_fields_changed:
            await _recompute_effective_dates(session, account)
        await session.commit()
        await session.refresh(account)
        return account

    for key, value in update_data.items():
        setattr(account, key, value)

    if account.type != "credit_card":
        account.credit_limit = None
        account.statement_close_day = None
        account.payment_due_day = None
        account.minimum_payment = None
        account.card_brand = None
        account.card_level = None

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
                apply_effective_date(opening_tx, account)
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
                apply_effective_date(opening_tx, account)
                session.add(opening_tx)
        elif opening_tx:
            await session.delete(opening_tx)

    if cycle_fields_changed:
        await _recompute_effective_dates(session, account)

    await session.commit()
    await session.refresh(account)
    return account


async def _recompute_effective_dates(session: AsyncSession, account: Account) -> None:
    """Recompute effective_date on every transaction in this account.

    Called when an account's CC cycle metadata (statement_close_day,
    payment_due_day) changes, so historical transactions get rebucketed into
    the correct bill. Cheap: a few hundred rows per account at most."""
    result = await session.execute(
        select(Transaction).where(Transaction.account_id == account.id)
    )
    for tx in result.scalars():
        apply_effective_date(tx, account)


async def sync_opening_balance_for_connected_account(
    session: AsyncSession, account: Account
) -> None:
    """Reconcile the opening_balance transaction so SUM(all txs) = account.balance.

    Providers (Pluggy etc.) typically only return ~1 year of history, so the sum
    of imported transactions rarely equals the account's true current balance.
    This helper computes the missing opening balance and upserts a synthetic
    `source='opening_balance'` transaction that closes the gap. After this runs,
    balance_history and running-balance walks line up with the card balance.

    Call after adding new transactions in a sync (initial or incremental).
    Does not commit; the caller is responsible for the transaction boundary.
    """
    if account.connection_id is None:
        return

    # For connected CC accounts the stored balance is positive debt and the UI
    # displays it negated (account_service.serialize_account). The sum of signed
    # transaction amounts on a CC trends negative as debt accrues, so the target
    # we want SUM(signed txs) to hit is -balance. For every other account type
    # the target is simply the stored balance.
    is_cc = account.type == "credit_card"
    target = -account.balance if is_cc else account.balance

    effective_amount = case(
        (Transaction.currency == account.currency, Transaction.amount),
        else_=func.coalesce(Transaction.amount_primary, Transaction.amount),
    )
    signed_amount = case(
        (Transaction.type == "credit", effective_amount),
        else_=-effective_amount,
    )

    sum_result = await session.execute(
        select(func.coalesce(func.sum(signed_amount), 0)).where(
            Transaction.account_id == account.id,
            Transaction.source != "opening_balance",
        )
    )
    tx_sum = Decimal(str(sum_result.scalar() or 0))

    offset = Decimal(str(target)) - tx_sum

    existing = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    existing_tx = existing.scalar_one_or_none()

    # Offsets below one cent are rounding noise; drop any stale opening tx.
    if abs(offset) < Decimal("0.01"):
        if existing_tx:
            await session.delete(existing_tx)
        return

    oldest_result = await session.execute(
        select(func.min(Transaction.date)).where(
            Transaction.account_id == account.id,
            Transaction.source != "opening_balance",
        )
    )
    oldest_date = oldest_result.scalar()
    opening_date = (oldest_date - timedelta(days=1)) if oldest_date else _Date.today()

    # Sign convention matches the rest of the codebase: credit = +, debit = -
    # regardless of account type. A positive offset needs a credit to raise the
    # running sum to target; a negative offset needs a debit.
    opening_type = "credit" if offset > 0 else "debit"
    amount = abs(offset).quantize(Decimal("0.01"))

    if existing_tx:
        existing_tx.amount = amount
        existing_tx.type = opening_type
        existing_tx.date = opening_date
        existing_tx.currency = account.currency
        apply_effective_date(existing_tx, account)
    else:
        opening_tx = Transaction(
            user_id=account.user_id,
            account_id=account.id,
            description="Saldo inicial",
            amount=amount,
            currency=account.currency,
            date=opening_date,
            type=opening_type,
            source="opening_balance",
        )
        apply_effective_date(opening_tx, account)
        session.add(opening_tx)
    await session.flush()


async def delete_account(session: AsyncSession, account_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    account = await get_account(session, account_id, user_id)
    if not account:
        return False

    # Only allow deleting manual accounts
    if account.connection_id is not None:
        raise ValueError("Cannot delete bank-connected accounts")

    # Clean up attachment files for all transactions in this account
    from app.services.attachment_service import cleanup_attachment_files
    from app.models.import_log import ImportLog
    from app.models.recurring_transaction import RecurringTransaction
    from app.models.goal import Goal
    tx_result = await session.execute(
        select(Transaction.id).where(Transaction.account_id == account_id)
    )
    tx_ids = [row[0] for row in tx_result.all()]
    await cleanup_attachment_files(session, tx_ids)

    # Break FK references before deleting the account. In production these are
    # also enforced at the DB level (see migration 039) — this code path makes
    # the behavior explicit and keeps the FK bug from #110 from regressing for
    # any of the dependent tables.
    #
    # Order matters: transactions imported from a file reference import_logs
    # via transactions.import_id. We must null that out *before* deleting the
    # log rows, otherwise the log delete trips transactions_import_id_fkey.
    # The transaction rows themselves cascade-delete via Account.transactions
    # when session.delete(account) flushes below.
    await session.execute(
        Transaction.__table__.update()
        .where(Transaction.account_id == account_id)
        .values(import_id=None)
    )
    await session.execute(
        ImportLog.__table__.delete().where(ImportLog.account_id == account_id)
    )
    await session.execute(
        RecurringTransaction.__table__.delete().where(
            RecurringTransaction.account_id == account_id
        )
    )
    await session.execute(
        Goal.__table__.update()
        .where(Goal.account_id == account_id)
        .values(account_id=None)
    )

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

    # Keep `connection_id` intact for connected accounts so the sync loop in
    # connection_service can find the account by (connection_id, external_id)
    # and honor the `is_closed` skip. Unlinking caused the next sync to treat
    # the provider account as new and create a duplicate active row, while
    # leaving the original entry stranded in "Closed Accounts" with no link
    # back to its connection (issue #90).

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
    bill_id: Optional[uuid.UUID] = None,
    unbilled_only: bool = False,
) -> Optional[dict]:
    account = await get_account(session, account_id, user_id)
    if not account:
        return None

    today = _Date.today()
    if not date_from:
        date_from = today.replace(day=1)
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
            ).where(Transaction.account_id == account_id)
        )
        current_balance = float(balance_result.scalar())

    # Connected CC: provider balance is positive for debt → negate.
    # Manual CC: transaction math already gives negative for debt.
    if account.type == "credit_card" and account.connection_id:
        current_balance = -current_balance

    # Bucketing date: for credit-card txs the user can override which cycle
    # a tx belongs to via `effective_bill_date`. We honor that first so the
    # totals card and bar chart agree with the transactions list (issue #92).
    bucket_date = func.coalesce(Transaction.effective_bill_date, Transaction.date)

    # Bill-driven filter (issue #92): when the caller passes bill_id, include
    #   (a) txs linked to this bill via Pluggy's billId mapping, AND
    #   (b) txs with NO bill_id (manual entries, OFX/CSV imports, recurring
    #       fills) whose bucketing date is in the cycle window — without (b)
    #       we'd drop user-added compensations for missing provider txs.
    # Without bill_id (cycle-math or non-CC), apply the date window straight.
    from sqlalchemy import and_ as _and, not_ as _not  # local: only for scope
    # Resolve the active bill's due_date once so the pending-exclusion can
    # trust our cycle-math pre-classification (see get_transactions).
    active_due_subq = (
        select(CreditCardBill.due_date)
        .where(CreditCardBill.id == bill_id)
        .scalar_subquery()
    ) if bill_id is not None else None

    def _scope(query):
        if bill_id is not None:
            unlinked_in_window = _and(
                Transaction.bill_id.is_(None),
                # Defer sync-pending txs only when their effective_date does
                # NOT match this bill — i.e., cycle math placed them in a
                # different bill. If effective_date matches, the tx is
                # pre-classified to this bill and we include it (the
                # in-progress case abdalanervoso reported empty).
                _not(_and(
                    Transaction.source == "sync",
                    Transaction.status == "pending",
                    Transaction.effective_date != active_due_subq,
                )),
                bucket_date >= date_from,
                bucket_date <= date_to,
            )
            return query.where(or_(Transaction.bill_id == bill_id, unlinked_in_window))
        # Cycle-math fallback. Opt-in `unbilled_only` excludes already-billed
        # txs so an in-progress cycle's bar/total doesn't double-count past-
        # bill txs whose date falls in the window (see get_transactions).
        if unbilled_only:
            return query.where(
                Transaction.bill_id.is_(None),
                bucket_date >= date_from,
                bucket_date <= date_to,
            )
        return query.where(bucket_date >= date_from, bucket_date <= date_to)

    # Income = SUM of credit transactions in window (excluding opening_balance,
    # paired transfers, and transfer-like categories).
    income_result = await session.execute(
        _scope(select(func.coalesce(func.sum(effective_amount), 0)).where(
            Transaction.account_id == account_id,
            Transaction.type == "credit",
            Transaction.source != "opening_balance",
            counts_as_pnl(),
        ))
    )
    monthly_income = float(income_result.scalar())

    # Expenses = SUM of debit transactions in window (same exclusions).
    # For credit-card accounts, NET refund credits against debits so the
    # cycle's "Total da fatura" matches the bank's bill (refunds reduce the
    # invoice amount). counts_as_pnl already excludes paired transfers and
    # transfer-like categories, so bill payments are not double-counted.
    if account.type == "credit_card":
        signed_for_bill = case(
            (Transaction.type == "credit", -func.abs(effective_amount)),
            else_=func.abs(effective_amount),
        )
        expenses_result = await session.execute(
            _scope(select(func.coalesce(func.sum(signed_for_bill), 0)).where(
                Transaction.account_id == account_id,
                Transaction.source != "opening_balance",
                counts_as_pnl(),
            ))
        )
    else:
        expenses_result = await session.execute(
            _scope(select(func.coalesce(func.sum(func.abs(effective_amount)), 0)).where(
                Transaction.account_id == account_id,
                Transaction.type == "debit",
                counts_as_pnl(),
            ))
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
    if not date_from:
        date_from = today.replace(day=1)
    if not date_to:
        date_to = today

    sign = -1.0 if (account.type == "credit_card" and account.connection_id) else 1.0

    series = await _account_daily_balance_series(session, account_id, date_from, date_to, account.currency)

    if sign != 1.0:
        for point in series:
            point["balance"] = round(point["balance"] * sign, 2)

    return series
