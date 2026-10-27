import uuid
from datetime import date as _Date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    case,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.asset import LOAN_COLLATERAL_TYPES, Asset
from app.models.bank_connection import BankConnection
from app.models.credit_card_bill import CreditCardBill
from app.models.transaction import Transaction
from app.schemas.account import AccountCreate, AccountUpdate
from app.services._query_filters import (
    counts_as_pnl,
    counts_in_current_balance,
    counts_on_bill,
    is_confirmed,
    is_inside_provider_snapshot,
    is_not_future,
    is_not_ignored,
    reporting_date_col,
)
from app.services.credit_card_service import (
    apply_effective_date,
    cc_cycle_bill_due_dates,
    compute_available_credit,
    get_cycle_dates,
)
from app.services.fx_rate_service import _resolve_rate
from app.models.category import Category


def get_account_name(account: Account) -> str:
    return account.display_name or account.name


def _simplefin_to_internal_balance(provider: str, account_type: str, balance: Decimal) -> Decimal:
    """Normalize a SimpleFIN balance to Securo's positive-for-debt convention.

    SimpleFIN reports a credit card's balance as negative debt and exposes no
    account type, so the provider stores it raw and labels every account
    "checking". Pluggy/Enable report card debt as a positive number, which is
    the convention every downstream site (serialize_account, _account_balance_at,
    sync_opening_balance_for_connected_account, ...) assumes. Flip SimpleFIN card
    balances to match so those sites stay provider-agnostic.
    """
    if provider == "simplefin" and account_type == "credit_card":
        return -balance
    return balance


def _opening_balance_values(account_type: str, balance: Decimal) -> tuple[Decimal, str]:
    amount = abs(balance)
    # A loan is entered as the amount owed, like a card, and is debt in the ledger.
    is_credit = (balance > 0) == (account_type not in {"credit_card", "loan"})
    return amount, "credit" if is_credit else "debit"


def _balance_subqueries(today: _Date):
    """Per-account current and previous balances, as two subqueries.

    Shared by the list and the single-account read so both report the same
    number for the same account: the detail endpoint used to serialize with no
    balance at all, which made available_credit fall back to the full limit.
    """
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
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            counts_in_current_balance(today),
            Transaction.is_ignored == False,
            or_(
                Transaction.category_id.is_(None),
                Category.is_ignored == False,
            ),
        )
        .group_by(Transaction.account_id)
        .subquery()
    )

    # Subquery: compute previous_balance (balance at end of previous month)
    first_of_month = today.replace(day=1)
    prev_month_end = first_of_month - timedelta(days=1)

    prev_balance_sq = (
        select(
            Transaction.account_id,
            func.coalesce(func.sum(signed_amount), 0).label("previous_balance"),
        )
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.date <= prev_month_end,
            Transaction.status == "posted",
            Transaction.is_ignored == False,
            or_(
                Transaction.category_id.is_(None),
                Category.is_ignored == False,
            ),
        )
        .group_by(Transaction.account_id)
        .subquery()
    )

    # Subquery: charges dated after today (future installments, scheduled
    # purchases). They are not owed yet, so the balance leaves them out, but a
    # card's limit is already held for them (issue #1185).
    future_charges_sq = (
        select(
            Transaction.account_id,
            func.coalesce(func.sum(effective_amount), 0).label("future_charges"),
        )
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Account.type == "credit_card",
            Transaction.type == "debit",
            Transaction.date > today,
            Transaction.is_ignored == False,
            or_(
                Transaction.category_id.is_(None),
                Category.is_ignored == False,
            ),
        )
        .group_by(Transaction.account_id)
        .subquery()
    )

    # Subquery: the open bill of each card on a shared credit line (earliest
    # due_date on or after today; one row per account via rn = 1). Every card
    # on one consolidated line repeats the contract-wide balance, so the
    # sidebar shows the card's own bill instead (issue #684).
    open_bill_sq = (
        select(
            CreditCardBill.account_id,
            CreditCardBill.total_amount,
            func.row_number()
            .over(
                partition_by=CreditCardBill.account_id,
                order_by=CreditCardBill.due_date,
            )
            .label("rn"),
        )
        .join(Account, CreditCardBill.account_id == Account.id)
        .where(
            Account.type == "credit_card",
            Account.shared_balance_group.isnot(None),
            CreditCardBill.due_date >= today,
        )
        .subquery()
    )

    return balance_sq, prev_balance_sq, future_charges_sq, open_bill_sq


def _accounts_with_balances_query(
    workspace_id: uuid.UUID, balance_sq, prev_balance_sq, future_charges_sq, open_bill_sq
):
    return (
        select(
            Account,
            BankConnection,
            func.coalesce(balance_sq.c.current_balance, 0).label("current_balance"),
            func.coalesce(prev_balance_sq.c.previous_balance, 0).label("previous_balance"),
            func.coalesce(future_charges_sq.c.future_charges, 0).label("future_charges"),
            open_bill_sq.c.total_amount.label("open_bill_total"),
            # The opening balance's date, so the edit form can show it and
            # leave it alone (issue #1204).
            select(func.min(Transaction.date))
            .where(
                Transaction.account_id == Account.id,
                Transaction.source == "opening_balance",
            )
            .correlate(Account)
            .scalar_subquery()
            .label("balance_date"),
        )
        .outerjoin(BankConnection)
        .outerjoin(balance_sq, Account.id == balance_sq.c.account_id)
        .outerjoin(prev_balance_sq, Account.id == prev_balance_sq.c.account_id)
        .outerjoin(future_charges_sq, Account.id == future_charges_sq.c.account_id)
        .outerjoin(
            open_bill_sq,
            and_(Account.id == open_bill_sq.c.account_id, open_bill_sq.c.rn == 1),
        )
        .where(
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
            )
        )
    )


async def get_accounts(session: AsyncSession, workspace_id: uuid.UUID, include_closed: bool = False) -> list[dict]:
    query = _accounts_with_balances_query(workspace_id, *_balance_subqueries(app_today()))
    if not include_closed:
        query = query.where(Account.is_closed == False)
    query = query.order_by(Account.name)
    result = await session.execute(query)
    return [
            serialize_account(
                acc,
                current_balance,
                previous_balance,
                connection,
                future_charges=future_charges,
                open_bill_total=open_bill_total,
                balance_date=balance_date,
            )
            for (
                acc,
                connection,
                current_balance,
                previous_balance,
                future_charges,
                open_bill_total,
                balance_date,
            ) in result.all()
        ]


async def get_account_with_balances(
    session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[dict]:
    """One account, serialized with the same balances the list reports."""
    query = _accounts_with_balances_query(
        workspace_id, *_balance_subqueries(app_today())
    ).where(Account.id == account_id)
    row = (await session.execute(query)).first()
    if row is None:
        return None
    acc, connection, current_balance, previous_balance, future_charges, open_bill_total, balance_date = row
    return serialize_account(
        acc,
        current_balance,
        previous_balance,
        connection,
        future_charges=future_charges,
        open_bill_total=open_bill_total,
        balance_date=balance_date,
    )


def _institution(
    acc: Account, connection: Optional[BankConnection]
) -> tuple[Optional[str], Optional[str]]:
    # (name, logo) resolved as a pair so both always describe the same
    # institution. The account's own institution (SimpleFIN — issue #345)
    # only outranks a connection rename when the link actually spans several
    # institutions — on a single-bank link the rename keeps working. The logo
    # falls back to the connection's only on single-institution links, where
    # it belongs to the same bank; on multi links a missing favicon beats
    # another bank's.
    if acc.institution is None:
        if not connection:
            return None, None
        return connection.display_name or connection.institution_name, connection.logo_url
    if connection is None:
        return acc.institution.name, acc.institution.logo_url
    if len(connection.institutions) > 1:
        return acc.institution.name, acc.institution.logo_url
    name = connection.display_name or acc.institution.name
    return name, acc.institution.logo_url or connection.logo_url


def serialize_account(
    acc: Account,
    current_balance: Optional[Decimal],
    previous_balance: Optional[Decimal],
    connection: Optional[BankConnection] = None,
    *,
    future_charges: Optional[Decimal] = None,
    open_bill_total: Optional[Decimal] = None,
    balance_date: Optional[_Date] = None,
) -> dict:
    # Connected CC: provider stores positive for debt → negate.
    # Manual accounts: transaction math already gives correct sign.
    if acc.connection_id:
        resolved_balance = float(acc.balance) * (-1 if acc.type == "credit_card" else 1)
    else:
        resolved_balance = float(current_balance or 0)

    institution_name, institution_logo_url = _institution(acc, connection)
    payload = {
        "id": acc.id,
        "user_id": acc.user_id,
        "connection_id": acc.connection_id,
        "external_id": acc.external_id,
        "name": acc.name,
        "display_name": acc.display_name,
        "masked_number": acc.masked_number,
        "type": acc.type,
        "secured_asset_id": acc.secured_asset_id,
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
        "shared_balance_group": acc.shared_balance_group,
        # The card's own open bill, when it sits on a shared credit line.
        # Null for every other account and for shared cards without one.
        "open_bill_total": float(open_bill_total) if open_bill_total is not None else None,
        "open_bill_total_primary": None,
        "institution_name": institution_name,
        "institution_logo_url": institution_logo_url,
        "balance_date": balance_date,
        "available_credit": None,
        "next_close_date": None,
        "next_due_date": None,
    }

    if acc.type == "credit_card":
        # A manual card's limit also covers charges dated after today, such as
        # the remaining installments of a purchase. A connected card's
        # provider balance is used as-is.
        held = Decimal("0") if acc.connection_id else Decimal(str(future_charges or 0))
        available = compute_available_credit(
            acc.credit_limit, Decimal(str(resolved_balance)) - held
        )
        payload["available_credit"] = float(available) if available is not None else None
        cycle = get_cycle_dates(acc.statement_close_day, acc.payment_due_day)
        payload["next_close_date"] = cycle["next_close_date"]
        payload["next_due_date"] = cycle["next_due_date"]

    return payload


async def get_credit_card_bills(
    session: AsyncSession,
    account_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    limit: int = 24,
) -> Optional[list[CreditCardBill]]:
    """Return bills for a CC account, newest due_date first.

    Returns None when the account doesn't exist or isn't owned by the user
    (the caller maps that to a 404). Returns [] for non-CC accounts and CC
    accounts with no synced bills — the read path then keeps using the
    cycle-math fallback.
    """
    account = await get_account(session, account_id, workspace_id)
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


async def get_account(session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID) -> Optional[Account]:
    result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .options(contains_eager(Account.connection))
        .where(
            Account.id == account_id,
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
            ),
        )
    )
    return result.scalar_one_or_none()


async def create_account(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: AccountCreate,
) -> Account:
    await _validate_secured_asset(session, workspace_id, data.type, data.secured_asset_id)
    is_cc = data.type == "credit_card"
    account = Account(
        user_id=user_id,
        workspace_id=workspace_id,
        name=data.name,
        type=data.type,
        balance=data.balance,
        currency=data.currency,
        secured_asset_id=data.secured_asset_id,
        credit_limit=data.credit_limit if is_cc else None,
        statement_close_day=data.statement_close_day if is_cc else None,
        payment_due_day=data.payment_due_day if is_cc else None,
        minimum_payment=data.minimum_payment if is_cc else None,
        card_brand=data.card_brand if is_cc else None,
        card_level=data.card_level if is_cc else None,
    )
    session.add(account)
    await session.flush()  # get account.id without committing

    if data.balance != Decimal("0.00"):
        amount, opening_type = _opening_balance_values(data.type, data.balance)
        opening_tx = Transaction(
            user_id=user_id,
            workspace_id=workspace_id,
            account_id=account.id,
            description="Saldo inicial",
            amount=amount,
            currency=data.currency,
            date=data.balance_date or app_today(),
            type=opening_type,
            source="opening_balance",
        )
        apply_effective_date(opening_tx, account)
        session.add(opening_tx)

    await session.commit()
    await session.refresh(account)
    return account


async def update_account(
    session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID, data: AccountUpdate
) -> Optional[Account]:
    account = await get_account(session, account_id, workspace_id)
    if not account:
        return None

    update_data = data.model_dump(exclude_unset=True)
    balance_date = update_data.pop("balance_date", None)
    new_type = update_data.get("type", account.type)
    if account.connection_id is not None and new_type == "loan":
        raise ValueError("Bank-connected accounts cannot be loans yet")
    # Switching between a loan and any other type flips what the balance means
    # (money held vs. money owed). That is safe for an account that only has
    # its opening balance; with real history it would silently reinterpret it.
    if new_type != account.type and "loan" in {account.type, new_type}:
        has_history = await session.scalar(
            select(Transaction.id)
            .where(
                Transaction.account_id == account.id,
                Transaction.source != "opening_balance",
            )
            .limit(1)
        )
        if has_history is not None:
            raise ValueError("An account with transactions cannot switch to or from a loan")
    # The property link is dropped when the account stops being a loan, so only
    # a link sent with this edit is checked.
    await _validate_secured_asset(
        session,
        workspace_id,
        new_type,
        update_data.get(
            "secured_asset_id", account.secured_asset_id if new_type == "loan" else None
        ),
    )

    # Track whether we need to recompute effective_date for all transactions.
    # Changes to the CC cycle days shift which bill each historical purchase
    # belongs to, so stored effective_dates need to be rebuilt.
    cycle_fields_changed = any(
        k in update_data for k in ("statement_close_day", "payment_due_day")
    )
    old_type = account.type

    # Bank-connected accounts are managed by the sync pipeline. Beyond display
    # name and credit card metadata (limit + cycle days, which providers often
    # don't expose), users may also override the account `type` — providers
    # sometimes misreport it (e.g. Enable Banking labels an mBank savings
    # account as "checking"; issue #271). Sync only writes `type` on initial
    # account creation and never overwrites it afterwards, so the override
    # survives subsequent syncs without a separate field.
    if account.connection_id is not None:
        editable_fields = {
            "display_name",
            "type",
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
        old_type = account.type
        new_type = update_data.get("type", account.type)
        cc_fields = editable_fields - {"display_name", "type"}
        cc_update = {k: v for k, v in update_data.items() if k in cc_fields}
        if cc_update and new_type != "credit_card":
            raise ValueError("Credit card fields can only be set on credit card accounts")
        for key, value in update_data.items():
            setattr(account, key, value)
        # SimpleFIN stores a card's balance with the raw provider sign (negative
        # for debt) under type="checking". When the user flips the type across
        # the credit_card boundary, the downstream display sites start (or stop)
        # applying the positive-for-debt negation, so the stored value must flip
        # too — otherwise the card double-counts. Mirror the ingestion-time
        # normalization (_simplefin_to_internal_balance) here so the correction
        # is immediate, not deferred to the next sync. Load the provider via
        # session.get (identity-map hit, never a lazy-load that throws).
        if old_type != new_type and "credit_card" in (old_type, new_type):
            conn = (
                await session.get(BankConnection, account.connection_id)
                if account.connection_id is not None
                else None
            )
            if conn is not None and conn.provider == "simplefin":
                account.balance = -account.balance
        # If the override moves the account away from credit_card, drop any
        # stale card metadata so it isn't left half credit-card.
        if new_type != "credit_card":
            account.credit_limit = None
            account.statement_close_day = None
            account.payment_due_day = None
            account.minimum_payment = None
            account.card_brand = None
            account.card_level = None
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
    if account.type != "loan":
        account.secured_asset_id = None

    opening_balance_type_changed = old_type != account.type and "loan" in {old_type, account.type}
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

        if new_balance != Decimal("0.00"):
            amount, opening_type = _opening_balance_values(account.type, new_balance)
            if opening_tx:
                opening_tx.amount = amount
                opening_tx.type = opening_type
                if balance_date:
                    opening_tx.date = balance_date
                apply_effective_date(opening_tx, account)
            else:
                opening_tx = Transaction(
                    user_id=account.user_id,
                    workspace_id=account.workspace_id,
                    account_id=account_id,
                    description="Saldo inicial",
                    amount=amount,
                    currency=account.currency,
                    date=balance_date or app_today(),
                    type=opening_type,
                    source="opening_balance",
                )
                apply_effective_date(opening_tx, account)
                session.add(opening_tx)
        elif opening_tx:
            await session.delete(opening_tx)
    elif opening_balance_type_changed or balance_date:
        existing_opening = await session.execute(
            select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.source == "opening_balance",
            )
        )
        opening_tx = existing_opening.scalar_one_or_none()
        if opening_tx:
            if opening_balance_type_changed and account.balance != Decimal("0.00"):
                _, opening_tx.type = _opening_balance_values(account.type, account.balance)
            if balance_date:
                opening_tx.date = balance_date
            apply_effective_date(opening_tx, account)

    if cycle_fields_changed:
        await _recompute_effective_dates(session, account)

    await session.commit()
    await session.refresh(account)
    return account


async def _validate_secured_asset(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    account_type: str,
    asset_id: Optional[uuid.UUID],
) -> None:
    if asset_id is None:
        return
    if account_type != "loan":
        raise ValueError("Only loan accounts can be linked to a property")
    asset = await session.scalar(
        select(Asset).where(Asset.id == asset_id, Asset.workspace_id == workspace_id)
    )
    if asset is None or asset.type not in LOAN_COLLATERAL_TYPES:
        raise ValueError("Linked asset must be a property or vehicle in the same workspace")


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

    # The provider balance is a snapshot for today. Future-dated transactions
    # are projections and must not change the synthetic opening transaction;
    # otherwise a later-dated row can shift the opening balance even though it
    # is not part of the provider's current balance yet.
    balance_cutoff = app_today()

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
            Transaction.date <= balance_cutoff,
            Transaction.is_ignored == False,
            or_(
                Transaction.category_id.is_(None),
                Transaction.category_id.not_in(
                    select(Category.id).where(Category.is_ignored == True)
                ),
            ),
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
            Transaction.date <= balance_cutoff,
            Transaction.is_ignored == False,
            or_(
                Transaction.category_id.is_(None),
                Transaction.category_id.not_in(
                    select(Category.id).where(Category.is_ignored == True)
                ),
            ),
        )
    )
    oldest_date = oldest_result.scalar()
    opening_date = (oldest_date - timedelta(days=1)) if oldest_date else app_today()

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
            workspace_id=account.workspace_id,
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


async def delete_account(session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
    account = await get_account(session, account_id, workspace_id)
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
        update(Transaction)
        .where(Transaction.account_id == account_id)
        .values(import_id=None)
    )
    await session.execute(
        delete(ImportLog).where(ImportLog.account_id == account_id)
    )
    await session.execute(
        delete(RecurringTransaction).where(
            RecurringTransaction.account_id == account_id
        )
    )
    await session.execute(
        update(Goal)
        .where(Goal.account_id == account_id)
        .values(account_id=None)
    )

    await session.delete(account)
    await session.commit()
    return True


async def close_account(
    session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[Account]:
    account = await get_account(session, account_id, workspace_id)
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
    session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID
) -> Optional[Account]:
    account = await get_account(session, account_id, workspace_id)
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
    session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID,
    date_from: Optional[_Date] = None, date_to: Optional[_Date] = None,
    bill_id: Optional[uuid.UUID] = None,
    unbilled_only: bool = False,
) -> Optional[dict]:
    account = await get_account(session, account_id, workspace_id)
    if not account:
        return None

    today = app_today()
    # Captured before the defaults below fill in None dates, so the
    # bill-based window rule only applies to windows the caller actually
    # sent (issue #563).
    explicit_window = date_from is not None and date_to is not None
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
            ).where(
                Transaction.account_id == account_id,
                is_not_future(today),
                # Same carve-out as the accounts list: a card's balance is the
                # debt owed and an authorized purchase is already owed. The
                # account is loaded here, so branch in Python rather than SQL.
                *([] if account.type == "credit_card" else [is_confirmed()]),
                Transaction.is_ignored == False,
                or_(
                    Transaction.category_id.is_(None),
                    Transaction.category_id.not_in(
                        select(Category.id).where(Category.is_ignored == True)
                    ),
                ),
            )
        )
        current_balance = float(balance_result.scalar() or 0)

    # Connected CC: provider balance is positive for debt → negate.
    # Manual CC: transaction math already gives negative for debt.
    if account.type == "credit_card" and account.connection_id:
        current_balance = -current_balance

    # Bucketing date: for credit-card txs the user can override which cycle
    # a tx belongs to via `effective_bill_date`. We honor that first so the
    # totals card and bar chart agree with the transactions list (issue #92).
    bucket_date = func.coalesce(Transaction.effective_bill_date, Transaction.date)

    # Bill-based membership for the plain (cycle-math) window: when the
    # caller sent a window that is exactly one bill cycle of this credit
    # card account, an overridden tx belongs to the bill whose
    # (previous due date, due date] range contains the override, not to the
    # purchase window that contains it (for due-before-close cards a due
    # date sits in the NEXT cycle's window, issue #563).
    cycle = None
    if (
        explicit_window
        and bill_id is None
        and not unbilled_only
        and account.type == "credit_card"
    ):
        cycle = cc_cycle_bill_due_dates(
            date_from, date_to,
            account.statement_close_day, account.payment_due_day,
        )

    def _membership():
        """Window predicate for these totals: the plain date window, or
        the bill-based rule above when the window is one of this
        account's cycles. The actuals cap (bucket_date <= today) and the
        forecast condition stay on the queries, so a tx in one bill's
        window can still not leak into another bill's totals."""
        if cycle is None or cycle["degenerate"]:
            return and_(bucket_date >= date_from, bucket_date <= date_to)
        override = Transaction.effective_bill_date
        return or_(
            and_(
                override.is_(None),
                bucket_date >= date_from,
                bucket_date <= date_to,
            ),
            # (previous due date, this due date]: each override date maps
            # to exactly one bill.
            and_(override > cycle["prev"], override <= cycle["this"]),
        )

    def _before_window():
        """Rows that belong to an earlier cycle than this one, under the
        same rule as `_membership`: without a cycle, anything bucketed
        before the window; with one, rows without an override bucketed
        before the window plus overrides on or before the previous bill's
        due date. Keeps older debt and this cycle's rows disjoint, so a
        charge moved onto the previous bill counts as older debt."""
        if cycle is None or cycle["degenerate"]:
            return bucket_date < date_from
        override = Transaction.effective_bill_date
        return or_(
            and_(override.is_(None), bucket_date < date_from),
            override <= cycle["prev"],
        )

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
                #
                # Manual override (effective_bill_date) bypasses the
                # exclusion entirely — the user explicitly hand-corrected
                # the bucketing, so the totals must reflect that even if
                # the override doesn't snap to a real bill due_date and
                # bill_id stays null (issue #162). Mirrors the same
                # carve-out in get_transactions.
                _not(_and(
                    Transaction.source == "sync",
                    Transaction.status == "pending",
                    Transaction.effective_bill_date.is_(None),
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
            # Forward-pointing override catch (issue #162): mirror
            # get_transactions so the in-progress cycle's totals include
            # txs whose manual override points past the cycle window.
            # Without this the tx list and totals diverge — the tx shows
            # in the list (after the catch in get_transactions) but its
            # amount drops out of the strip pill / summary card.
            future_override = _and(
                Transaction.effective_bill_date.is_not(None),
                Transaction.effective_bill_date > date_to,
            )
            return query.where(
                Transaction.bill_id.is_(None),
                or_(
                    _and(bucket_date >= date_from, bucket_date <= date_to),
                    future_override,
                ),
            )
        # Plain cycle-math window: bill-based membership when the window is
        # one of this credit card account's cycles (issue #563), plain date
        # window otherwise (non-CC accounts, wider ranges, degenerate
        # close/due pairs).
        return query.where(_membership())

    # Which exclusions these totals answer to. A credit card's four summary
    # numbers are all bill-side — they describe what the bank put on the
    # statement, so they keep purchases in `treat_as_transfer` categories
    # that the reporting filter drops. Every other account type keeps the
    # reporting view. See `counts_on_bill` for why the bill cannot simply
    # reuse `counts_as_pnl`.
    summary_filter = (
        counts_on_bill() if account.type == "credit_card" else counts_as_pnl()
    )

    # Intermediate payments into the card (the credit leg of a paired
    # transfer) reduce the bill total, but only by what is left after
    # they settle the debt that was already outstanding when the window
    # opened. Banks apply repayments oldest first: paying last cycle's
    # bill inside this window must not read as this cycle's charges
    # disappearing, while anything paid beyond that older debt can only
    # settle this cycle's charges, so the bank bills the net (issue #668).
    prior_debt = 0.0
    paid_now = 0.0
    paid_later = 0.0
    if account.type == "credit_card":
        prior_result = await session.execute(
            select(func.coalesce(func.sum(
                case(
                    (Transaction.type == "credit", effective_amount),
                    else_=-effective_amount,
                )
            ), 0)).where(
                Transaction.account_id == account_id,
                _before_window(),
                # Rows linked to the selected bill belong to it whatever
                # their date (see `_scope`), so they are not older debt.
                *([] if bill_id is None else [or_(
                    Transaction.bill_id.is_(None), Transaction.bill_id != bill_id,
                )]),
                Transaction.is_ignored == False,
                or_(
                    Transaction.category_id.is_(None),
                    Transaction.category_id.not_in(
                        select(Category.id).where(Category.is_ignored == True)
                    ),
                ),
            )
        )
        prior_debt = max(0.0, -float(prior_result.scalar() or 0))
        paired_payment = and_(
            counts_on_bill(net_payments=True),
            Transaction.type == "credit",
            Transaction.transfer_pair_id.is_not(None),
            Transaction.source != "opening_balance",
        )
        paid_now_result = await session.execute(
            _scope(select(func.coalesce(func.sum(func.abs(effective_amount)), 0)).where(
                Transaction.account_id == account_id,
                bucket_date <= today,
                Transaction.status == "posted",
                paired_payment,
            ))
        )
        paid_now = float(paid_now_result.scalar() or 0)
        paid_later_result = await session.execute(
            _scope(select(func.coalesce(func.sum(func.abs(effective_amount)), 0)).where(
                Transaction.account_id == account_id,
                or_(Transaction.status == "pending", bucket_date > today),
                paired_payment,
            ))
        )
        paid_later = float(paid_later_result.scalar() or 0)
    # What the posted payments leave over after the older debt, and what
    # the pending/future ones add on top of that.
    netted_now = max(0.0, paid_now - prior_debt)
    netted_total = max(0.0, paid_now + paid_later - prior_debt)

    # Income = SUM of credit transactions in window (excluding opening_balance
    # and paired transfers).
    income_result = await session.execute(
        _scope(select(func.coalesce(func.sum(effective_amount), 0)).where(
            Transaction.account_id == account_id,
            Transaction.type == "credit",
            Transaction.source != "opening_balance",
            bucket_date <= today,
            Transaction.status == "posted",
            summary_filter,
        ))
    )
    monthly_income = float(income_result.scalar())

    # Expenses = SUM of debit transactions in window (same exclusions).
    # For credit-card accounts, NET refund credits against debits so the
    # cycle's "Total da fatura" matches the bank's bill (refunds reduce
    # the invoice amount). Paired transfers are dropped by `transfer_pair_id`
    # and netted separately below (see `netted_now`); unpaired card payments
    # are dropped by the credit-side `treat_as_transfer` exclusion in
    # `counts_on_bill` (see its docstring for why that's asymmetric with the
    # debit side).
    if account.type == "credit_card":
        signed_for_bill = case(
            (Transaction.type == "credit", -func.abs(effective_amount)),
            else_=func.abs(effective_amount),
        )
        expenses_result = await session.execute(
            _scope(select(func.coalesce(func.sum(signed_for_bill), 0)).where(
                Transaction.account_id == account_id,
                Transaction.source != "opening_balance",
                bucket_date <= today,
                Transaction.status == "posted",
                summary_filter,
            ))
        )
    else:
        expenses_result = await session.execute(
            _scope(select(func.coalesce(func.sum(func.abs(effective_amount)), 0)).where(
                Transaction.account_id == account_id,
                Transaction.type == "debit",
                bucket_date <= today,
                Transaction.status == "posted",
                summary_filter,
            ))
        )
    monthly_expenses = float(expenses_result.scalar()) - netted_now

    # Forecast values use the same cycle/date scope as the actual totals, but
    # include every pending row and every row bucketed after today. The current
    # balance remains posted-only (or the provider number for connected
    # accounts); these fields are the payable/forecast view consumed by the
    # account-detail bill card.
    forecast_condition = or_(
        Transaction.status == "pending",
        bucket_date > today,
    )
    forecast_income_result = await session.execute(
        _scope(select(func.coalesce(func.sum(effective_amount), 0)).where(
            Transaction.account_id == account_id,
            Transaction.type == "credit",
            Transaction.source != "opening_balance",
            forecast_condition,
            summary_filter,
        ))
    )
    forecast_income = float(forecast_income_result.scalar() or 0)

    if account.type == "credit_card":
        forecast_expense_result = await session.execute(
            _scope(select(func.coalesce(func.sum(signed_for_bill), 0)).where(
                Transaction.account_id == account_id,
                Transaction.source != "opening_balance",
                forecast_condition,
                summary_filter,
            ))
        )
    else:
        forecast_expense_result = await session.execute(
            _scope(select(func.coalesce(func.sum(func.abs(effective_amount)), 0)).where(
                Transaction.account_id == account_id,
                Transaction.type == "debit",
                forecast_condition,
                summary_filter,
            ))
        )
    forecast_expenses = float(forecast_expense_result.scalar() or 0) - (
        netted_total - netted_now
    )

    opening_balance = (
        await get_period_opening_balance(session, account, date_from)
        if account.type != "credit_card"
        else 0.0
    )

    return {
        "account_id": account_id,
        "current_balance": current_balance,
        "opening_balance": opening_balance,
        "monthly_income": monthly_income,
        "monthly_expenses": monthly_expenses,
        "projected_income": monthly_income + forecast_income,
        "projected_expenses": monthly_expenses + forecast_expenses,
    }


async def get_period_opening_balance(
    session: AsyncSession, account: Account, date_from: _Date,
) -> float:
    """Projected balance of a non-credit-card account at (date_from - 1 day).

    It seeds the account-detail running-balance walk, so it includes pending
    rows and future-dated rows that occur before the visible window. The
    opening row itself is included when it falls inside the window and applied
    by the same walk as every other transaction. Shared by the summary, the
    list's `running_balance` and the running-balance series so all three start
    from the same number (#1202).
    """
    today = app_today()
    # Use amount_primary only when tx currency differs from account currency
    effective_amount = case(
        (Transaction.currency == account.currency, Transaction.amount),
        else_=func.coalesce(Transaction.amount_primary, Transaction.amount),
    )
    ob_base_filters = [
        Transaction.account_id == account.id,
        Transaction.is_ignored == False,
        or_(
            Transaction.category_id.is_(None),
            Transaction.category_id.not_in(
                select(Category.id).where(Category.is_ignored == True)
            ),
        ),
    ]
    if account.connection_id:
        # Connected current balance is the provider snapshot. Reconstruct
        # the balance before the visible window from that snapshot and all
        # settled/pending rows in the visible historical portion. This
        # preserves the provider number even when it includes pending rows.
        # A recurring placeholder is left out of that unwinding: we
        # generated it ourselves so it was never in the snapshot, and
        # removing it here would cancel it out when the walk re-applies it.
        period_filters = [
            *ob_base_filters,
            Transaction.date >= date_from,
            Transaction.date <= today,
        ]
        posted_result = await session.execute(
            select(func.coalesce(func.sum(
                case(
                    (Transaction.type == "credit", effective_amount),
                    else_=-effective_amount,
                )
            ), 0)).where(*period_filters, Transaction.status == "posted")
        )
        pending_result = await session.execute(
            select(func.coalesce(func.sum(
                case(
                    (Transaction.type == "credit", effective_amount),
                    else_=-effective_amount,
                )
            ), 0)).where(
                *period_filters,
                Transaction.status == "pending",
                is_inside_provider_snapshot(),
            )
        )
        opening_balance = float(account.balance) - float(posted_result.scalar() or 0)
        opening_balance -= float(pending_result.scalar() or 0)

        if date_from > today:
            before_window_result = await session.execute(
                select(func.coalesce(func.sum(
                    case(
                        (Transaction.type == "credit", effective_amount),
                        else_=-effective_amount,
                    )
                ), 0)).where(
                    *ob_base_filters,
                    Transaction.date > today,
                    Transaction.date < date_from,
                    Transaction.status.in_(("posted", "pending")),
                )
            )
            opening_balance += float(before_window_result.scalar() or 0)
        return opening_balance

    manual_result = await session.execute(
        select(func.coalesce(func.sum(
            case(
                (Transaction.type == "credit", effective_amount),
                else_=-effective_amount,
            )
        ), 0)).where(
            *ob_base_filters,
            Transaction.date < date_from,
            Transaction.status.in_(("posted", "pending")),
        )
    )
    return float(manual_result.scalar() or 0)


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
    """Get balance for a single account at a specific date.
    Excludes ignored transactions from the balance calculation."""
    result = await session.execute(
        select(func.coalesce(func.sum(_signed_amount_expr(account_currency)), 0))
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.account_id == account_id,
            Transaction.date <= min(cutoff, app_today()),
            Transaction.status == "posted",
            Transaction.is_ignored == False,
            or_(
                Transaction.category_id.is_(None),
                Category.is_ignored == False,
            ),
        )
    )
    return float(result.scalar() or 0)


async def _account_daily_balance_series(
    session: AsyncSession, account_id: uuid.UUID,
    date_from: _Date, date_to: _Date,
    account_currency: str = "",
) -> list[dict]:
    """Build daily balance series for [date_from, date_to] inclusive.
    Excludes ignored transactions from balance calculations."""
    # Get balance at end of day before range start
    start_balance = await _account_balance_at(session, account_id, date_from - timedelta(days=1), account_currency)

    # Get daily deltas within range: group by actual date
    # Exclude ignored transactions from daily deltas
    result = await session.execute(
        select(
            Transaction.date,
            func.sum(_signed_amount_expr(account_currency)),
        )
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.account_id == account_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.date <= app_today(),
            Transaction.status == "posted",
            Transaction.is_ignored == False,
            or_(
                Transaction.category_id.is_(None),
                Category.is_ignored == False,
            ),
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
    session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID,
    date_from: Optional[_Date] = None, date_to: Optional[_Date] = None,
) -> Optional[list[dict]]:
    account = await get_account(session, account_id, workspace_id)
    if not account:
        return None

    today = app_today()
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


def _running_balance_delta(account_currency: str):
    """A row's effect on the account-detail running balance (#1202).

    Shared by the transaction list and the daily series so a row moves both by
    the same amount. Ignored rows, by flag or by category, stay listed but move
    nothing, as in `get_period_opening_balance`. Status and date are not
    looked at: the walk is a projection, so pending and future-dated rows count.
    """
    return case((is_not_ignored(), _signed_amount_expr(account_currency)), else_=0)


def _running_balance_delta_primary(primary_currency: str):
    """`_running_balance_delta` in the user's primary currency.

    A foreign row with no FX stamp moves nothing rather than being counted
    1:1, the same call the account page made when it walked the rows itself.
    """
    amount = case(
        (Transaction.amount_primary.is_not(None), Transaction.amount_primary),
        (Transaction.currency == primary_currency, Transaction.amount),
        else_=0,
    )
    signed = case((Transaction.type == "credit", amount), else_=-amount)
    return case((is_not_ignored(), signed), else_=0)


async def _running_balance_seed(
    session: AsyncSession, account: Account, date_from: _Date, primary_currency: str,
) -> tuple[Decimal, Optional[Decimal]]:
    """Opening balance, and its primary-currency value when the account is in
    another currency, converted like the summary's `opening_balance_primary`:
    with a real rate only, and left unset without one (#1165)."""
    opening = Decimal(str(await get_period_opening_balance(session, account, date_from)))
    if account.currency == primary_currency:
        return opening, None
    rate = await _resolve_rate(session, account.currency, primary_currency)
    if rate is None:
        return opening, None
    return opening, (opening * rate).quantize(Decimal("0.01"))


async def get_running_balances(
    session: AsyncSession, account: Account, filtered_ids: Select, order_col: ColumnElement,
    date_from: _Date, row_ids: list[uuid.UUID], primary_currency: str,
) -> dict[uuid.UUID, tuple[float, Optional[float]]]:
    """Balance right after each of `row_ids`, walking the whole filtered list.

    `filtered_ids` selects the ids of every row the list matched, not just the
    page, so a row's value does not depend on which page it was loaded on
    (#1202). The window orders by the exact reverse of the list's default
    order, so the newest row carries the balance at the end of the range.
    """
    opening, opening_primary = await _running_balance_seed(
        session, account, date_from, primary_currency,
    )
    order_by = (order_col.asc(), Transaction.created_at.asc(), Transaction.id.asc())
    columns = [
        Transaction.id,
        func.sum(_running_balance_delta(account.currency))
        .over(order_by=order_by, rows=(None, 0))
        .label("walk"),
    ]
    if opening_primary is not None:
        columns.append(
            func.sum(_running_balance_delta_primary(primary_currency))
            .over(order_by=order_by, rows=(None, 0))
            .label("walk_primary")
        )
    walk = select(*columns).where(Transaction.id.in_(filtered_ids)).subquery()
    result = await session.execute(select(walk).where(walk.c.id.in_(row_ids)))

    balances: dict[uuid.UUID, tuple[float, Optional[float]]] = {}
    for row in result.mappings():
        balance = float(opening + Decimal(str(row["walk"] or 0)))
        balance_primary = (
            float(opening_primary + Decimal(str(row["walk_primary"] or 0)))
            if opening_primary is not None
            else None
        )
        balances[row["id"]] = (balance, balance_primary)
    return balances


async def get_account_running_balance(
    session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID,
    primary_currency: str, date_from: _Date, date_to: _Date,
    accounting_mode: Optional[str] = None,
) -> Optional[list[dict]]:
    """End-of-day balance for every day in [date_from, date_to] (#1202).

    The same walk as the list's `running_balance` over the unfiltered account
    list for that range, opening row included, so the last point equals the
    newest row's value. Unlike `get_account_balance_history`, pending and
    future-dated rows count: this is the account page's projected balance.
    """
    account = await get_account(session, account_id, workspace_id)
    if not account:
        return None
    # A card's page walks its bill cycle client-side; its balance is the debt.
    if account.type == "credit_card":
        raise ValueError("Running balance is not available for credit card accounts")

    opening, opening_primary = await _running_balance_seed(
        session, account, date_from, primary_currency,
    )
    # Bucket by the list's date column so each row lands on the day it is
    # listed under.
    date_col = reporting_date_col(accounting_mode or "cash")
    result = await session.execute(
        select(
            date_col,
            func.sum(_running_balance_delta(account.currency)),
            func.sum(_running_balance_delta_primary(primary_currency)),
        )
        .where(
            Transaction.account_id == account.id,
            Transaction.workspace_id == workspace_id,
            date_col >= date_from,
            date_col <= date_to,
        )
        .group_by(date_col)
    )
    deltas = {
        day: (Decimal(str(delta or 0)), Decimal(str(delta_primary or 0)))
        for day, delta, delta_primary in result.all()
    }

    series = []
    balance, balance_primary = opening, opening_primary
    current = date_from
    while current <= date_to:
        delta, delta_primary = deltas.get(current, (Decimal(0), Decimal(0)))
        balance += delta
        if balance_primary is not None:
            balance_primary += delta_primary
        series.append({
            "date": current,
            "balance": float(balance),
            "balance_primary": float(balance_primary) if balance_primary is not None else None,
        })
        current += timedelta(days=1)
    return series
