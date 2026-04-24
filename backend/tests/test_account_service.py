"""Service-level tests for account_service.

Directly exercises the service functions to ensure full coverage of:
- create_account (with balance, zero balance, credit_card type)
- update_account (rename, balance change, sync opening_balance, bank-connected rejection)
- delete_account (manual, bank-connected rejection, not found)
- close_account / reopen_account
- get_account_summary (manual, bank-connected, credit_card, date range)
- get_account_balance_history
- _account_balance_at / _account_daily_balance_series
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.goal import Goal
from app.models.import_log import ImportLog
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.account import AccountCreate, AccountUpdate
from app.services.account_service import (
    create_account,
    close_account,
    delete_account,
    get_account,
    get_account_balance_history,
    get_account_summary,
    get_accounts,
    reopen_account,
    update_account,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_account(
    session: AsyncSession, user_id: uuid.UUID,
    name: str = "Test Account", acc_type: str = "checking",
    balance: str = "0.00", currency: str = "BRL",
    connection_id: uuid.UUID | None = None,
    external_id: str | None = None,
) -> Account:
    account = Account(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        type=acc_type,
        balance=Decimal(balance),
        currency=currency,
        connection_id=connection_id,
        external_id=external_id,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _add_txn(
    session: AsyncSession, user_id: uuid.UUID, account_id: uuid.UUID,
    amount: float, txn_type: str, txn_date: date,
    source: str = "manual", transfer_pair_id: uuid.UUID | None = None,
) -> Transaction:
    from datetime import datetime, timezone
    txn = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        account_id=account_id,
        description=f"Test {txn_type} {amount}",
        amount=Decimal(str(amount)),
        date=txn_date,
        type=txn_type,
        source=source,
        currency="BRL",
        transfer_pair_id=transfer_pair_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


# ---------------------------------------------------------------------------
# create_account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_account_with_balance(session: AsyncSession, test_user):
    """Creating an account with balance > 0 creates an opening_balance transaction."""
    data = AccountCreate(name="Checking", type="checking", balance=Decimal("1000.00"), currency="BRL")
    account = await create_account(session, test_user.id, data)

    assert account.name == "Checking"
    assert account.balance == Decimal("1000.00")

    # Verify opening_balance transaction was created
    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    opening = result.scalar_one_or_none()
    assert opening is not None
    assert opening.amount == Decimal("1000.00")
    assert opening.type == "credit"


@pytest.mark.asyncio
async def test_create_credit_card_account_opening_is_debit(session: AsyncSession, test_user):
    """Credit card opening balance is recorded as debit (represents debt)."""
    data = AccountCreate(name="Nubank", type="credit_card", balance=Decimal("500.00"), currency="BRL")
    account = await create_account(session, test_user.id, data)

    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    opening = result.scalar_one()
    assert opening.type == "debit"
    assert opening.amount == Decimal("500.00")


@pytest.mark.asyncio
async def test_create_account_zero_balance_no_opening(session: AsyncSession, test_user):
    """Creating an account with zero balance creates no opening transaction."""
    data = AccountCreate(name="Empty", type="checking", balance=Decimal("0.00"), currency="BRL")
    account = await create_account(session, test_user.id, data)

    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_create_account_with_balance_date(session: AsyncSession, test_user):
    """Opening transaction uses the provided balance_date."""
    custom_date = date(2025, 1, 15)
    data = AccountCreate(
        name="Dated", type="checking", balance=Decimal("2000.00"),
        currency="BRL", balance_date=custom_date,
    )
    account = await create_account(session, test_user.id, data)

    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    opening = result.scalar_one()
    assert opening.date == custom_date


# ---------------------------------------------------------------------------
# update_account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_account_name(session: AsyncSession, test_user):
    """Updating account name works for manual accounts."""
    account = await _make_account(session, test_user.id, "Old Name")
    data = AccountUpdate(name="New Name")
    updated = await update_account(session, account.id, test_user.id, data)

    assert updated is not None
    assert updated.name == "New Name"


@pytest.mark.asyncio
async def test_update_account_balance_creates_opening(session: AsyncSession, test_user):
    """Updating balance on an account with no opening_balance creates one."""
    account = await _make_account(session, test_user.id, "No Balance", balance="0.00")
    data = AccountUpdate(balance=Decimal("500.00"))
    updated = await update_account(session, account.id, test_user.id, data)

    assert updated is not None
    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    opening = result.scalar_one()
    assert opening.amount == Decimal("500.00")
    assert opening.type == "credit"


@pytest.mark.asyncio
async def test_update_account_balance_updates_existing_opening(session: AsyncSession, test_user):
    """Updating balance when opening_balance exists updates it."""
    data = AccountCreate(name="Update Test", type="checking", balance=Decimal("1000.00"), currency="BRL")
    account = await create_account(session, test_user.id, data)

    update_data = AccountUpdate(balance=Decimal("2000.00"))
    await update_account(session, account.id, test_user.id, update_data)

    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    opening = result.scalar_one()
    assert opening.amount == Decimal("2000.00")


@pytest.mark.asyncio
async def test_update_account_balance_to_zero_removes_opening(session: AsyncSession, test_user):
    """Setting balance to 0 removes the opening_balance transaction."""
    data = AccountCreate(name="Zero Test", type="checking", balance=Decimal("500.00"), currency="BRL")
    account = await create_account(session, test_user.id, data)

    update_data = AccountUpdate(balance=Decimal("0.00"))
    await update_account(session, account.id, test_user.id, update_data)

    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_update_account_balance_with_date(session: AsyncSession, test_user):
    """Updating balance with balance_date updates the opening tx date."""
    data = AccountCreate(name="Date Test", type="checking", balance=Decimal("1000.00"), currency="BRL")
    account = await create_account(session, test_user.id, data)

    new_date = date(2025, 6, 15)
    update_data = AccountUpdate(balance=Decimal("1500.00"), balance_date=new_date)
    await update_account(session, account.id, test_user.id, update_data)

    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    opening = result.scalar_one()
    assert opening.date == new_date
    assert opening.amount == Decimal("1500.00")


@pytest.mark.asyncio
async def test_update_bank_connected_raises(session: AsyncSession, test_user, test_connection):
    """Updating a bank-connected account raises ValueError."""
    account = await _make_account(
        session, test_user.id, "Connected",
        connection_id=test_connection.id, external_id="ext-1",
    )
    data = AccountUpdate(name="Hacked")
    with pytest.raises(ValueError, match="bank-connected"):
        await update_account(session, account.id, test_user.id, data)


@pytest.mark.asyncio
async def test_update_account_not_found(session: AsyncSession, test_user):
    """Updating nonexistent account returns None."""
    data = AccountUpdate(name="Ghost")
    result = await update_account(session, uuid.uuid4(), test_user.id, data)
    assert result is None


# ---------------------------------------------------------------------------
# delete_account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_manual_account(session: AsyncSession, test_user):
    """Deleting a manual account returns True."""
    account = await _make_account(session, test_user.id, "To Delete")
    result = await delete_account(session, account.id, test_user.id)
    assert result is True

    # Verify it's gone
    assert await get_account(session, account.id, test_user.id) is None


@pytest.mark.asyncio
async def test_delete_bank_connected_raises(session: AsyncSession, test_user, test_connection):
    """Deleting a bank-connected account raises ValueError."""
    account = await _make_account(
        session, test_user.id, "Connected",
        connection_id=test_connection.id, external_id="ext-del",
    )
    with pytest.raises(ValueError, match="bank-connected"):
        await delete_account(session, account.id, test_user.id)


@pytest.mark.asyncio
async def test_delete_account_not_found(session: AsyncSession, test_user):
    """Deleting nonexistent account returns False."""
    result = await delete_account(session, uuid.uuid4(), test_user.id)
    assert result is False


@pytest.mark.asyncio
async def test_delete_account_with_import_logs(session: AsyncSession, test_user):
    """Regression (#110): deleting an account with import_logs must succeed and
    cascade-delete the orphaned log rows instead of tripping the FK constraint."""
    from sqlalchemy import select

    account = await _make_account(session, test_user.id, "With Imports")
    log = ImportLog(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        filename="stmt.ofx", format="ofx", transaction_count=3,
    )
    session.add(log)
    await session.commit()
    log_id = log.id

    result = await delete_account(session, account.id, test_user.id)
    assert result is True

    assert await get_account(session, account.id, test_user.id) is None
    orphan = await session.execute(
        select(ImportLog).where(ImportLog.id == log_id)
    )
    assert orphan.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_account_with_recurring_transactions(session: AsyncSession, test_user):
    """Regression (#110, @stanleyndachi): deleting an account with a recurring
    transaction must succeed; the recurring rows cascade away since a schedule
    without an account can't post."""
    from sqlalchemy import select

    account = await _make_account(session, test_user.id, "With Recurring")
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        description="Rent", amount=Decimal("1000.00"), currency="BRL",
        type="debit", frequency="monthly",
        start_date=date.today(), next_occurrence=date.today(),
    )
    session.add(rec)
    await session.commit()
    rec_id = rec.id

    result = await delete_account(session, account.id, test_user.id)
    assert result is True

    orphan = await session.execute(
        select(RecurringTransaction).where(RecurringTransaction.id == rec_id)
    )
    assert orphan.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_account_with_imported_transactions(session: AsyncSession, test_user):
    """Regression (#110 v2, @ivancarlosti): deleting an account whose transactions
    were imported from a file must succeed. The imported rows reference the
    import_log via transactions.import_id — deleting import_logs first would
    trip transactions_import_id_fkey until that reference is cleared."""
    from sqlalchemy import select

    account = await _make_account(session, test_user.id, "Imported Txns")
    log = ImportLog(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        filename="stmt.ofx", format="ofx", transaction_count=1,
    )
    session.add(log)
    await session.flush()

    tx = await _add_txn(
        session, test_user.id, account.id,
        amount=42.0, txn_type="debit", txn_date=date.today(),
        source="import",
    )
    tx.import_id = log.id
    await session.commit()
    log_id = log.id
    tx_id = tx.id

    result = await delete_account(session, account.id, test_user.id)
    assert result is True

    assert await get_account(session, account.id, test_user.id) is None

    session.expire_all()
    orphan_log = await session.execute(
        select(ImportLog).where(ImportLog.id == log_id)
    )
    assert orphan_log.scalar_one_or_none() is None
    orphan_tx = await session.execute(
        select(Transaction).where(Transaction.id == tx_id)
    )
    assert orphan_tx.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_account_with_linked_goal(session: AsyncSession, test_user):
    """Regression (#110): deleting an account tracked by a goal must succeed;
    the goal survives with account_id nulled out (progress history is kept)."""
    from sqlalchemy import select

    account = await _make_account(session, test_user.id, "Goal Tracked")
    goal = Goal(
        id=uuid.uuid4(), user_id=test_user.id, name="Emergency fund",
        target_amount=Decimal("10000.00"), current_amount=Decimal("2500.00"),
        currency="BRL", tracking_type="account", account_id=account.id,
    )
    session.add(goal)
    await session.commit()
    goal_id = goal.id

    result = await delete_account(session, account.id, test_user.id)
    assert result is True

    session.expire_all()
    surviving = await session.execute(
        select(Goal).where(Goal.id == goal_id)
    )
    kept = surviving.scalar_one()
    assert kept.account_id is None
    assert kept.current_amount == Decimal("2500.00")


# ---------------------------------------------------------------------------
# close_account / reopen_account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_account(session: AsyncSession, test_user):
    """Closing a manual account sets is_closed and closed_at."""
    account = await _make_account(session, test_user.id, "To Close")
    closed = await close_account(session, account.id, test_user.id)

    assert closed is not None
    assert closed.is_closed is True
    assert closed.closed_at is not None


@pytest.mark.asyncio
async def test_close_bank_connected_keeps_link(session: AsyncSession, test_user, test_connection):
    """Closing a bank-connected account keeps its connection link.

    Sync uses (connection_id, external_id) to find the row; unlinking on close
    caused sync to create a duplicate active account (issue #90). The is_closed
    flag alone is enough to keep sync from touching it.
    """
    account = await _make_account(
        session, test_user.id, "Connected Close",
        connection_id=test_connection.id, external_id="ext-close",
    )
    closed = await close_account(session, account.id, test_user.id)
    assert closed.connection_id == test_connection.id
    assert closed.is_closed is True


@pytest.mark.asyncio
async def test_close_already_closed_raises(session: AsyncSession, test_user):
    """Closing an already-closed account raises ValueError."""
    account = await _make_account(session, test_user.id, "Already Closed")
    await close_account(session, account.id, test_user.id)

    with pytest.raises(ValueError, match="already closed"):
        await close_account(session, account.id, test_user.id)


@pytest.mark.asyncio
async def test_close_not_found(session: AsyncSession, test_user):
    """Closing nonexistent account returns None."""
    result = await close_account(session, uuid.uuid4(), test_user.id)
    assert result is None


@pytest.mark.asyncio
async def test_reopen_account(session: AsyncSession, test_user):
    """Reopening a closed account clears is_closed and closed_at."""
    account = await _make_account(session, test_user.id, "Reopen Test")
    await close_account(session, account.id, test_user.id)

    reopened = await reopen_account(session, account.id, test_user.id)
    assert reopened is not None
    assert reopened.is_closed is False
    assert reopened.closed_at is None


@pytest.mark.asyncio
async def test_reopen_not_closed_raises(session: AsyncSession, test_user):
    """Reopening a non-closed account raises ValueError."""
    account = await _make_account(session, test_user.id, "Open")
    with pytest.raises(ValueError, match="not closed"):
        await reopen_account(session, account.id, test_user.id)


@pytest.mark.asyncio
async def test_reopen_not_found(session: AsyncSession, test_user):
    """Reopening nonexistent account returns None."""
    result = await reopen_account(session, uuid.uuid4(), test_user.id)
    assert result is None


# ---------------------------------------------------------------------------
# get_accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_accounts_returns_list(session: AsyncSession, test_user):
    """get_accounts returns list with current_balance and previous_balance."""
    account = await _make_account(session, test_user.id, "List Test", balance="1000.00")
    await _add_txn(session, test_user.id, account.id, 1000, "credit", date.today(), source="opening_balance")

    accounts = await get_accounts(session, test_user.id)
    assert len(accounts) >= 1
    acc = next(a for a in accounts if a["id"] == account.id)
    assert acc["name"] == "List Test"
    assert "current_balance" in acc
    assert "previous_balance" in acc


@pytest.mark.asyncio
async def test_get_accounts_excludes_closed(session: AsyncSession, test_user):
    """get_accounts excludes closed accounts by default."""
    account = await _make_account(session, test_user.id, "Closed Account")
    await close_account(session, account.id, test_user.id)

    accounts = await get_accounts(session, test_user.id)
    ids = [a["id"] for a in accounts]
    assert account.id not in ids


@pytest.mark.asyncio
async def test_get_accounts_includes_closed_when_requested(session: AsyncSession, test_user):
    """get_accounts includes closed accounts when include_closed=True."""
    account = await _make_account(session, test_user.id, "Closed Visible")
    await close_account(session, account.id, test_user.id)

    accounts = await get_accounts(session, test_user.id, include_closed=True)
    ids = [a["id"] for a in accounts]
    assert account.id in ids


@pytest.mark.asyncio
async def test_get_accounts_credit_card_negated_balance(session: AsyncSession, test_user, test_connection):
    """Bank-connected credit_card current_balance is negated."""
    account = await _make_account(
        session, test_user.id, "CC Connected",
        acc_type="credit_card", balance="3000.00",
        connection_id=test_connection.id, external_id="ext-cc",
    )
    accounts = await get_accounts(session, test_user.id)
    cc = next(a for a in accounts if a["id"] == account.id)
    # For bank-connected CC: current_balance = -balance
    assert cc["current_balance"] == pytest.approx(-3000.0)


# ---------------------------------------------------------------------------
# get_account_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_account_summary_manual(session: AsyncSession, test_user):
    """Summary for manual account computes balance from transactions."""
    account = await _make_account(session, test_user.id, "Summary Test")
    today = date.today()

    # Add opening balance and some transactions
    await _add_txn(session, test_user.id, account.id, 5000, "credit", today, source="opening_balance")
    await _add_txn(session, test_user.id, account.id, 200, "debit", today)
    await _add_txn(session, test_user.id, account.id, 100, "credit", today)

    summary = await get_account_summary(session, account.id, test_user.id)
    assert summary is not None
    assert summary["current_balance"] == pytest.approx(4900.0)  # 5000 - 200 + 100
    assert summary["monthly_income"] == pytest.approx(100.0)  # excludes opening_balance
    assert summary["monthly_expenses"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_get_account_summary_bank_connected(session: AsyncSession, test_user, test_connection):
    """Summary for bank-connected account uses stored balance."""
    account = await _make_account(
        session, test_user.id, "Connected Summary",
        balance="7500.00",
        connection_id=test_connection.id, external_id="ext-sum",
    )
    summary = await get_account_summary(session, account.id, test_user.id)
    assert summary is not None
    assert summary["current_balance"] == pytest.approx(7500.0)


@pytest.mark.asyncio
async def test_get_account_summary_credit_card_bank(session: AsyncSession, test_user, test_connection):
    """Bank-connected credit_card summary negates balance."""
    account = await _make_account(
        session, test_user.id, "CC Bank",
        acc_type="credit_card", balance="2000.00",
        connection_id=test_connection.id, external_id="ext-cc-sum",
    )
    summary = await get_account_summary(session, account.id, test_user.id)
    assert summary is not None
    assert summary["current_balance"] == pytest.approx(-2000.0)


@pytest.mark.asyncio
async def test_get_account_summary_with_date_range(session: AsyncSession, test_user):
    """Summary filters income/expenses by date range."""
    account = await _make_account(session, test_user.id, "Date Range Test")
    today = date.today()
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=15)

    await _add_txn(session, test_user.id, account.id, 1000, "credit", last_month)
    await _add_txn(session, test_user.id, account.id, 500, "credit", today)

    # Query only this month
    summary = await get_account_summary(
        session, account.id, test_user.id,
        date_from=today.replace(day=1), date_to=today,
    )
    assert summary is not None
    assert summary["monthly_income"] == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_get_account_summary_excludes_transfers(session: AsyncSession, test_user):
    """Summary excludes transfer pair transactions from income/expenses."""
    account = await _make_account(session, test_user.id, "Transfer Exclude")
    today = date.today()
    pair_id = uuid.uuid4()

    await _add_txn(session, test_user.id, account.id, 300, "debit", today, transfer_pair_id=pair_id)
    await _add_txn(session, test_user.id, account.id, 100, "debit", today)

    summary = await get_account_summary(session, account.id, test_user.id)
    assert summary is not None
    # Only the non-transfer debit counts
    assert summary["monthly_expenses"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_get_account_summary_not_found(session: AsyncSession, test_user):
    """Summary for nonexistent account returns None."""
    result = await get_account_summary(session, uuid.uuid4(), test_user.id)
    assert result is None


# ---------------------------------------------------------------------------
# get_account_balance_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_account_balance_history(session: AsyncSession, test_user):
    """Balance history returns daily balance series."""
    account = await _make_account(session, test_user.id, "History Test")
    today = date.today()

    await _add_txn(session, test_user.id, account.id, 1000, "credit", today.replace(day=1), source="opening_balance")
    await _add_txn(session, test_user.id, account.id, 200, "debit", today.replace(day=min(5, today.day)))

    history = await get_account_balance_history(
        session, account.id, test_user.id,
        date_from=today.replace(day=1), date_to=today,
    )
    assert history is not None
    assert len(history) > 0
    # Each entry has date and balance
    assert "date" in history[0]
    assert "balance" in history[0]


@pytest.mark.asyncio
async def test_get_account_balance_history_not_found(session: AsyncSession, test_user):
    """Balance history for nonexistent account returns None."""
    result = await get_account_balance_history(session, uuid.uuid4(), test_user.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_account_balance_history_default_dates(session: AsyncSession, test_user):
    """Balance history uses current month if no dates provided."""
    account = await _make_account(session, test_user.id, "Default Dates")
    await _add_txn(session, test_user.id, account.id, 1000, "credit", date.today(), source="opening_balance")

    history = await get_account_balance_history(session, account.id, test_user.id)
    assert history is not None
    assert len(history) > 0


@pytest.mark.asyncio
async def test_get_account_balance_history_credit_card_negated(
    session: AsyncSession, test_user, test_connection,
):
    """Balance history for bank-connected credit_card applies sign negation."""
    account = await _make_account(
        session, test_user.id, "CC History",
        acc_type="credit_card", balance="1000.00",
        connection_id=test_connection.id, external_id="ext-cc-hist",
    )
    today = date.today()
    await _add_txn(session, test_user.id, account.id, 500, "debit", today)

    history = await get_account_balance_history(
        session, account.id, test_user.id,
        date_from=today, date_to=today,
    )
    assert history is not None
    assert len(history) == 1
    # CC with connection_id has sign=-1.0: a debit (spending) on CC
    # produces negative balance, negated to positive (showing debt increase)
    assert history[0]["balance"] == pytest.approx(500.0)
