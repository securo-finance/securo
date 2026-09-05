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
    _simplefin_to_internal_balance,
    create_account,
    close_account,
    delete_account,
    get_account,
    get_account_balance_history,
    get_account_summary,
    get_accounts,
    reopen_account,
    serialize_account,
    sync_opening_balance_for_connected_account,
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
    status: str = "posted",
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
        status=status,
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
async def test_create_account_with_balance(session: AsyncSession, test_user, test_workspace):
    """Creating an account with balance > 0 creates an opening_balance transaction."""
    data = AccountCreate(name="Checking", type="checking", balance=Decimal("1000.00"), currency="BRL")
    account = await create_account(session, test_workspace.id, test_user.id, data)

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
async def test_create_account_with_negative_balance(session: AsyncSession, test_user, test_workspace):
    """Negative manual opening balance is recorded as a debit."""
    data = AccountCreate(name="Overdrawn", type="checking", balance=Decimal("-250.00"), currency="BRL")
    account = await create_account(session, test_workspace.id, test_user.id, data)

    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    opening = result.scalar_one()
    assert opening.amount == Decimal("250.00")
    assert opening.type == "debit"

    [serialized] = await get_accounts(session, test_workspace.id)
    assert serialized["current_balance"] == -250.0


@pytest.mark.asyncio
async def test_create_credit_card_account_opening_is_debit(session: AsyncSession, test_user, test_workspace):
    """Credit card opening balance is recorded as debit (represents debt)."""
    data = AccountCreate(name="Nubank", type="credit_card", balance=Decimal("500.00"), currency="BRL")
    account = await create_account(session, test_workspace.id, test_user.id, data)

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
async def test_create_account_zero_balance_no_opening(session: AsyncSession, test_user, test_workspace):
    """Creating an account with zero balance creates no opening transaction."""
    data = AccountCreate(name="Empty", type="checking", balance=Decimal("0.00"), currency="BRL")
    account = await create_account(session, test_workspace.id, test_user.id, data)

    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_create_account_with_balance_date(session: AsyncSession, test_user, test_workspace):
    """Opening transaction uses the provided balance_date."""
    custom_date = date(2025, 1, 15)
    data = AccountCreate(
        name="Dated", type="checking", balance=Decimal("2000.00"),
        currency="BRL", balance_date=custom_date,
    )
    account = await create_account(session, test_workspace.id, test_user.id, data)

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
async def test_update_account_name(session: AsyncSession, test_user, test_workspace):
    """Updating account name works for manual accounts."""
    account = await _make_account(session, test_user.id, "Old Name")
    data = AccountUpdate(name="New Name")
    updated = await update_account(session, account.id, test_workspace.id, data)

    assert updated is not None
    assert updated.name == "New Name"


@pytest.mark.asyncio
async def test_update_account_balance_creates_opening(session: AsyncSession, test_user, test_workspace):
    """Updating balance on an account with no opening_balance creates one."""
    account = await _make_account(session, test_user.id, "No Balance", balance="0.00")
    data = AccountUpdate(balance=Decimal("500.00"))
    updated = await update_account(session, account.id, test_workspace.id, data)

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
async def test_update_account_balance_creates_negative_opening(session: AsyncSession, test_user, test_workspace):
    """Updating a manual account to a negative balance creates a debit opening."""
    account = await _make_account(session, test_user.id, "No Balance", balance="0.00")
    data = AccountUpdate(balance=Decimal("-500.00"))
    updated = await update_account(session, account.id, test_workspace.id, data)

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
    assert opening.type == "debit"


@pytest.mark.asyncio
async def test_update_account_balance_updates_existing_opening(session: AsyncSession, test_user, test_workspace):
    """Updating balance when opening_balance exists updates it."""
    data = AccountCreate(name="Update Test", type="checking", balance=Decimal("1000.00"), currency="BRL")
    account = await create_account(session, test_workspace.id, test_user.id, data)

    update_data = AccountUpdate(balance=Decimal("2000.00"))
    await update_account(session, account.id, test_workspace.id, update_data)

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
async def test_update_account_balance_to_zero_removes_opening(session: AsyncSession, test_user, test_workspace):
    """Setting balance to 0 removes the opening_balance transaction."""
    data = AccountCreate(name="Zero Test", type="checking", balance=Decimal("500.00"), currency="BRL")
    account = await create_account(session, test_workspace.id, test_user.id, data)

    update_data = AccountUpdate(balance=Decimal("0.00"))
    await update_account(session, account.id, test_workspace.id, update_data)

    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_update_account_balance_with_date(session: AsyncSession, test_user, test_workspace):
    """Updating balance with balance_date updates the opening tx date."""
    data = AccountCreate(name="Date Test", type="checking", balance=Decimal("1000.00"), currency="BRL")
    account = await create_account(session, test_workspace.id, test_user.id, data)

    new_date = date(2025, 6, 15)
    update_data = AccountUpdate(balance=Decimal("1500.00"), balance_date=new_date)
    await update_account(session, account.id, test_workspace.id, update_data)

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
async def test_update_account_balance_date_only_updates_opening(session: AsyncSession, test_user, test_workspace):
    """Updating only balance_date moves the existing opening transaction."""
    data = AccountCreate(name="Date Only Test", type="checking", balance=Decimal("1000.00"), currency="BRL")
    account = await create_account(session, test_workspace.id, test_user.id, data)

    new_date = date(2025, 7, 20)
    update_data = AccountUpdate(balance_date=new_date)
    await update_account(session, account.id, test_workspace.id, update_data)

    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    opening = result.scalar_one()
    assert opening.date == new_date
    assert opening.amount == Decimal("1000.00")


@pytest.mark.asyncio
async def test_update_bank_connected_raises(session: AsyncSession, test_user, test_workspace, test_connection):
    """Updating a bank-connected account raises ValueError."""
    account = await _make_account(
        session, test_user.id, "Connected",
        connection_id=test_connection.id, external_id="ext-1",
    )
    data = AccountUpdate(name="Hacked")
    with pytest.raises(ValueError, match="bank-connected"):
        await update_account(session, account.id, test_workspace.id, data)


@pytest.mark.asyncio
async def test_update_bank_connected_type_override(
    session: AsyncSession, test_user, test_workspace, test_connection
):
    """The account type can be overridden on a bank-connected account (issue #271)."""
    account = await _make_account(
        session, test_user.id, "mBank Savings", acc_type="checking",
        connection_id=test_connection.id, external_id="ext-type",
    )
    updated = await update_account(
        session, account.id, test_workspace.id, AccountUpdate(type="savings")
    )
    assert updated is not None
    assert updated.type == "savings"


@pytest.mark.asyncio
async def test_update_bank_connected_type_override_clears_card_metadata(
    session: AsyncSession, test_user, test_workspace, test_connection
):
    """Overriding a connected card to a non-card type drops stale card metadata."""
    account = await _make_account(
        session, test_user.id, "Was a card", acc_type="credit_card",
        connection_id=test_connection.id, external_id="ext-card",
    )
    account.credit_limit = Decimal("5000.00")
    account.statement_close_day = 10
    await session.commit()

    updated = await update_account(
        session, account.id, test_workspace.id, AccountUpdate(type="checking")
    )
    assert updated is not None
    assert updated.type == "checking"
    assert updated.credit_limit is None
    assert updated.statement_close_day is None


@pytest.mark.asyncio
async def test_update_account_not_found(session: AsyncSession, test_user, test_workspace):
    """Updating nonexistent account returns None."""
    data = AccountUpdate(name="Ghost")
    result = await update_account(session, uuid.uuid4(), test_workspace.id, data)
    assert result is None


# ---------------------------------------------------------------------------
# delete_account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_manual_account(session: AsyncSession, test_user, test_workspace):
    """Deleting a manual account returns True."""
    account = await _make_account(session, test_user.id, "To Delete")
    result = await delete_account(session, account.id, test_workspace.id)
    assert result is True

    # Verify it's gone
    assert await get_account(session, account.id, test_workspace.id) is None


@pytest.mark.asyncio
async def test_delete_bank_connected_raises(session: AsyncSession, test_user, test_workspace, test_connection):
    """Deleting a bank-connected account raises ValueError."""
    account = await _make_account(
        session, test_user.id, "Connected",
        connection_id=test_connection.id, external_id="ext-del",
    )
    with pytest.raises(ValueError, match="bank-connected"):
        await delete_account(session, account.id, test_workspace.id)


@pytest.mark.asyncio
async def test_delete_account_not_found(session: AsyncSession, test_user, test_workspace):
    """Deleting nonexistent account returns False."""
    result = await delete_account(session, uuid.uuid4(), test_workspace.id)
    assert result is False


@pytest.mark.asyncio
async def test_delete_account_with_import_logs(session: AsyncSession, test_user, test_workspace):
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

    result = await delete_account(session, account.id, test_workspace.id)
    assert result is True

    assert await get_account(session, account.id, test_workspace.id) is None
    orphan = await session.execute(
        select(ImportLog).where(ImportLog.id == log_id)
    )
    assert orphan.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_account_with_recurring_transactions(session: AsyncSession, test_user, test_workspace):
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

    result = await delete_account(session, account.id, test_workspace.id)
    assert result is True

    orphan = await session.execute(
        select(RecurringTransaction).where(RecurringTransaction.id == rec_id)
    )
    assert orphan.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_account_with_imported_transactions(session: AsyncSession, test_user, test_workspace):
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

    result = await delete_account(session, account.id, test_workspace.id)
    assert result is True

    assert await get_account(session, account.id, test_workspace.id) is None

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
async def test_delete_account_with_linked_goal(session: AsyncSession, test_user, test_workspace):
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

    result = await delete_account(session, account.id, test_workspace.id)
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
async def test_close_account(session: AsyncSession, test_user, test_workspace):
    """Closing a manual account sets is_closed and closed_at."""
    account = await _make_account(session, test_user.id, "To Close")
    closed = await close_account(session, account.id, test_workspace.id)

    assert closed is not None
    assert closed.is_closed is True
    assert closed.closed_at is not None


@pytest.mark.asyncio
async def test_close_bank_connected_keeps_link(session: AsyncSession, test_user, test_workspace, test_connection):
    """Closing a bank-connected account keeps its connection link.

    Sync uses (connection_id, external_id) to find the row; unlinking on close
    caused sync to create a duplicate active account (issue #90). The is_closed
    flag alone is enough to keep sync from touching it.
    """
    account = await _make_account(
        session, test_user.id, "Connected Close",
        connection_id=test_connection.id, external_id="ext-close",
    )
    closed = await close_account(session, account.id, test_workspace.id)

    assert closed is not None
    assert closed.connection_id == test_connection.id
    assert closed.is_closed is True


@pytest.mark.asyncio
async def test_close_already_closed_raises(session: AsyncSession, test_user, test_workspace):
    """Closing an already-closed account raises ValueError."""
    account = await _make_account(session, test_user.id, "Already Closed")
    await close_account(session, account.id, test_workspace.id)

    with pytest.raises(ValueError, match="already closed"):
        await close_account(session, account.id, test_workspace.id)


@pytest.mark.asyncio
async def test_close_not_found(session: AsyncSession, test_user, test_workspace):
    """Closing nonexistent account returns None."""
    result = await close_account(session, uuid.uuid4(), test_workspace.id)
    assert result is None


@pytest.mark.asyncio
async def test_reopen_account(session: AsyncSession, test_user, test_workspace):
    """Reopening a closed account clears is_closed and closed_at."""
    account = await _make_account(session, test_user.id, "Reopen Test")
    await close_account(session, account.id, test_workspace.id)

    reopened = await reopen_account(session, account.id, test_workspace.id)
    assert reopened is not None
    assert reopened.is_closed is False
    assert reopened.closed_at is None


@pytest.mark.asyncio
async def test_reopen_not_closed_raises(session: AsyncSession, test_user, test_workspace):
    """Reopening a non-closed account raises ValueError."""
    account = await _make_account(session, test_user.id, "Open")
    with pytest.raises(ValueError, match="not closed"):
        await reopen_account(session, account.id, test_workspace.id)


@pytest.mark.asyncio
async def test_reopen_not_found(session: AsyncSession, test_user, test_workspace):
    """Reopening nonexistent account returns None."""
    result = await reopen_account(session, uuid.uuid4(), test_workspace.id)
    assert result is None


# ---------------------------------------------------------------------------
# get_accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_accounts_returns_list(session: AsyncSession, test_user, test_workspace):
    """get_accounts returns list with current_balance and previous_balance."""
    account = await _make_account(session, test_user.id, "List Test", balance="1000.00")
    await _add_txn(session, test_user.id, account.id, 1000, "credit", date.today(), source="opening_balance")

    accounts = await get_accounts(session, test_workspace.id)
    assert len(accounts) >= 1
    acc = next(a for a in accounts if a["id"] == account.id)
    assert acc["name"] == "List Test"
    assert "current_balance" in acc
    assert "previous_balance" in acc


@pytest.mark.asyncio
async def test_get_accounts_previous_balance_uses_each_account_currency(
    session: AsyncSession, test_user, test_workspace
):
    """Previous balances convert each transaction with its own account currency."""
    previous_month = date.today().replace(day=1) - timedelta(days=1)
    brl_account = await _make_account(session, test_user.id, "BRL Account", currency="BRL")
    usd_account = await _make_account(session, test_user.id, "USD Account", currency="USD")

    brl_txn = await _add_txn(
        session, test_user.id, brl_account.id, 100, "credit", previous_month
    )
    usd_txn = await _add_txn(
        session, test_user.id, usd_account.id, 20, "credit", previous_month
    )
    usd_txn.currency = "USD"
    brl_txn.amount_primary = Decimal("500.00")
    usd_txn.amount_primary = Decimal("100.00")
    await session.commit()

    accounts = await get_accounts(session, test_workspace.id)
    by_id = {account["id"]: account for account in accounts}

    assert by_id[brl_account.id]["previous_balance"] == pytest.approx(100.0)
    assert by_id[usd_account.id]["previous_balance"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_get_accounts_excludes_closed(session: AsyncSession, test_user, test_workspace):
    """get_accounts excludes closed accounts by default."""
    account = await _make_account(session, test_user.id, "Closed Account")
    await close_account(session, account.id, test_workspace.id)

    accounts = await get_accounts(session, test_workspace.id)
    ids = [a["id"] for a in accounts]
    assert account.id not in ids


@pytest.mark.asyncio
async def test_get_accounts_includes_closed_when_requested(session: AsyncSession, test_user, test_workspace):
    """get_accounts includes closed accounts when include_closed=True."""
    account = await _make_account(session, test_user.id, "Closed Visible")
    await close_account(session, account.id, test_workspace.id)

    accounts = await get_accounts(session, test_workspace.id, include_closed=True)
    ids = [a["id"] for a in accounts]
    assert account.id in ids


@pytest.mark.asyncio
async def test_get_accounts_credit_card_negated_balance(session: AsyncSession, test_user, test_workspace, test_connection):
    """Bank-connected credit_card current_balance is negated."""
    account = await _make_account(
        session, test_user.id, "CC Connected",
        acc_type="credit_card", balance="3000.00",
        connection_id=test_connection.id, external_id="ext-cc",
    )
    accounts = await get_accounts(session, test_workspace.id)
    cc = next(a for a in accounts if a["id"] == account.id)
    # For bank-connected CC: current_balance = -balance
    assert cc["current_balance"] == pytest.approx(-3000.0)


# ---------------------------------------------------------------------------
# get_account_summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_account_summary_manual(session: AsyncSession, test_user, test_workspace):
    """Summary for manual account computes balance from transactions."""
    account = await _make_account(session, test_user.id, "Summary Test")
    today = date.today()

    # Add opening balance and some transactions
    await _add_txn(session, test_user.id, account.id, 5000, "credit", today, source="opening_balance")
    await _add_txn(session, test_user.id, account.id, 200, "debit", today)
    await _add_txn(session, test_user.id, account.id, 100, "credit", today)

    summary = await get_account_summary(session, account.id, test_workspace.id)
    assert summary is not None
    assert summary["current_balance"] == pytest.approx(4900.0)  # 5000 - 200 + 100
    assert summary["monthly_income"] == pytest.approx(100.0)  # excludes opening_balance
    assert summary["monthly_expenses"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_get_account_summary_bank_connected(session: AsyncSession, test_user, test_workspace, test_connection):
    """Summary for bank-connected account uses stored balance."""
    account = await _make_account(
        session, test_user.id, "Connected Summary",
        balance="7500.00",
        connection_id=test_connection.id, external_id="ext-sum",
    )
    summary = await get_account_summary(session, account.id, test_workspace.id)
    assert summary is not None
    assert summary["current_balance"] == pytest.approx(7500.0)


@pytest.mark.asyncio
async def test_get_account_summary_credit_card_bank(session: AsyncSession, test_user, test_workspace, test_connection):
    """Bank-connected credit_card summary negates balance."""
    account = await _make_account(
        session, test_user.id, "CC Bank",
        acc_type="credit_card", balance="2000.00",
        connection_id=test_connection.id, external_id="ext-cc-sum",
    )
    summary = await get_account_summary(session, account.id, test_workspace.id)
    assert summary is not None
    assert summary["current_balance"] == pytest.approx(-2000.0)


@pytest.mark.asyncio
async def test_get_account_summary_with_date_range(session: AsyncSession, test_user, test_workspace):
    """Summary filters income/expenses by date range."""
    account = await _make_account(session, test_user.id, "Date Range Test")
    today = date.today()
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=15)

    await _add_txn(session, test_user.id, account.id, 1000, "credit", last_month)
    await _add_txn(session, test_user.id, account.id, 500, "credit", today)

    # Query only this month
    summary = await get_account_summary(
        session, account.id, test_workspace.id,
        date_from=today.replace(day=1), date_to=today,
    )
    assert summary is not None
    assert summary["monthly_income"] == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_get_account_summary_excludes_transfers(session: AsyncSession, test_user, test_workspace):
    """Summary excludes transfer pair transactions from income/expenses."""
    account = await _make_account(session, test_user.id, "Transfer Exclude")
    today = date.today()
    pair_id = uuid.uuid4()

    await _add_txn(session, test_user.id, account.id, 300, "debit", today, transfer_pair_id=pair_id)
    await _add_txn(session, test_user.id, account.id, 100, "debit", today)

    summary = await get_account_summary(session, account.id, test_workspace.id)
    assert summary is not None
    # Only the non-transfer debit counts
    assert summary["monthly_expenses"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_get_account_summary_not_found(session: AsyncSession, test_user, test_workspace):
    """Summary for nonexistent account returns None."""
    result = await get_account_summary(session, uuid.uuid4(), test_workspace.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_account_summary_excludes_pending_non_cc(session: AsyncSession, test_user, test_workspace):
    """Non-CC manual accounts exclude pending from current_balance.

    Mirrors the get_accounts behavior: credit 100 posted + debit 20 pending →
    displayed balance is 100 (pending debit dropped), not 80.
    """
    account = await _make_account(session, test_user.id, "Summary Pending")
    today = date.today()
    await _add_txn(session, test_user.id, account.id, 100, "credit", today)
    await _add_txn(session, test_user.id, account.id, 20, "debit", today, status="pending")

    summary = await get_account_summary(session, account.id, test_workspace.id)
    assert summary is not None
    assert summary["current_balance"] == pytest.approx(100.0)
    assert summary["monthly_expenses"] == pytest.approx(0.0)
    assert summary["projected_expenses"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_get_account_summary_keeps_pending_for_credit_card(session: AsyncSession, test_user, test_workspace):
    """A card's balance is the debt owed, and an authorized purchase is
    already owed. Keeping pending out would make the balance disagree with
    the card's own bill total, which includes it."""
    account = await _make_account(session, test_user.id, "Summary CC", acc_type="credit_card")
    today = date.today()
    await _add_txn(
        session, test_user.id, account.id, 100, "credit", today,
        source="opening_balance",
    )
    await _add_txn(session, test_user.id, account.id, 20, "debit", today, status="pending")

    summary = await get_account_summary(session, account.id, test_workspace.id)
    assert summary is not None
    # 100 opening minus the 20 authorized purchase.
    assert summary["current_balance"] == pytest.approx(80.0)
    # P&L still waits for the charge to settle.
    assert summary["monthly_expenses"] == pytest.approx(0.0)
    assert summary["projected_expenses"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_get_account_summary_opening_balance_connected_excludes_period_pending(
    session, test_user, test_workspace, test_connection
):
    """Connected non-CC opening balance backs out period pending so the
    frontend walk (opening + displayed period rows) does not double count them.

    Provider balance 780 = posted 500 (last month) + posted 300 + pending −20
    (this month). opening_balance must be 500 (= posted before period), not
    480 (= 780 − posted-in-period, which would leave pending counted twice).
    """
    account = await _make_account(
        session, test_user.id, "Conn Opening", balance="780.00",
        connection_id=test_connection.id,
    )
    today = date.today()
    month_start = today.replace(day=1)
    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    await _add_txn(session, test_user.id, account.id, 500, "credit", prev_month + timedelta(days=5))
    await _add_txn(
        session, test_user.id, account.id, 300, "credit",
        # Clamped like the pending row below: on the 1st of the month this
        # would otherwise land in the future, fall outside the period the
        # summary backs out, and leave the provider snapshot counting it.
        min(month_start + timedelta(days=2), today),
    )
    pending_day = min(month_start + timedelta(days=4), today)
    await _add_txn(session, test_user.id, account.id, 20, "debit", pending_day, status="pending")

    summary = await get_account_summary(
        session, account.id, test_workspace.id,
        date_from=month_start, date_to=today,
    )
    assert summary is not None
    assert summary["current_balance"] == pytest.approx(780.0)
    assert summary["opening_balance"] == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_get_account_summary_connected_keeps_recurring_pending_in_the_walk(
    session, test_user, test_workspace, test_connection
):
    """A recurring placeholder still moves the projected balance on a
    connected account.

    The provider's 780 covers only what it reported, so backing the
    placeholder out of the opening balance would cancel it against the walk
    that re-applies it: the charge would sit in the forecast totals while the
    projected balance ignored it.
    """
    account = await _make_account(
        session, test_user.id, "Conn Recurring", balance="780.00",
        connection_id=test_connection.id,
    )
    today = date.today()
    month_start = today.replace(day=1)
    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    await _add_txn(session, test_user.id, account.id, 500, "credit", prev_month + timedelta(days=5))
    await _add_txn(
        session, test_user.id, account.id, 300, "credit",
        # Clamped like the pending row below: on the 1st of the month this
        # would otherwise land in the future, fall outside the period the
        # summary backs out, and leave the provider snapshot counting it.
        min(month_start + timedelta(days=2), today),
    )
    await _add_txn(
        session, test_user.id, account.id, 20, "debit",
        min(month_start + timedelta(days=4), today),
        source="recurring", status="pending",
    )

    summary = await get_account_summary(
        session, account.id, test_workspace.id,
        date_from=month_start, date_to=today,
    )
    assert summary is not None
    # The provider snapshot is untouched.
    assert summary["current_balance"] == pytest.approx(780.0)
    # 780 − 300 posted in period, with the placeholder left in so the frontend
    # walk lands on 760 rather than back on 780.
    assert summary["opening_balance"] == pytest.approx(480.0)
    assert summary["monthly_expenses"] == pytest.approx(0.0)
    assert summary["projected_expenses"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_get_account_summary_connected_excludes_future_rows_from_opening_balance(
    session, test_user, test_workspace, test_connection
):
    """Future-dated rows remain projections and do not shift today's opening."""
    account = await _make_account(
        session, test_user.id, "Conn Future Rows", balance="780.00",
        connection_id=test_connection.id,
    )
    today = date.today()
    month_start = today.replace(day=1)
    prev_month = (month_start - timedelta(days=1)).replace(day=1)
    await _add_txn(session, test_user.id, account.id, 500, "credit", prev_month + timedelta(days=5))
    await _add_txn(
        session, test_user.id, account.id, 300, "credit",
        # Clamped like the pending row below: on the 1st of the month this
        # would otherwise land in the future, fall outside the period the
        # summary backs out, and leave the provider snapshot counting it.
        min(month_start + timedelta(days=2), today),
    )
    await _add_txn(
        session, test_user.id, account.id, 20, "debit",
        min(month_start + timedelta(days=4), today), status="pending",
    )
    await _add_txn(
        session, test_user.id, account.id, 1000, "credit",
        today + timedelta(days=10),
    )

    summary = await get_account_summary(
        session, account.id, test_workspace.id,
        date_from=month_start, date_to=today,
    )

    assert summary is not None
    assert summary["opening_balance"] == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_sync_opening_balance_ignores_future_and_ignored_rows(
    session, test_user, test_workspace, test_connection
):
    """Provider reconciliation uses only active rows dated through today."""
    account = await _make_account(
        session, test_user.id, "Sync Future Rows", balance="600.00",
        connection_id=test_connection.id,
    )
    today = date.today()
    await _add_txn(session, test_user.id, account.id, 500, "credit", today - timedelta(days=5))
    await _add_txn(session, test_user.id, account.id, 1000, "credit", today + timedelta(days=10))
    ignored = await _add_txn(session, test_user.id, account.id, 75, "debit", today - timedelta(days=2))
    ignored.is_ignored = True
    await session.commit()

    await sync_opening_balance_for_connected_account(session, account)
    from sqlalchemy import select
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.source == "opening_balance",
        )
    )
    opening = result.scalar_one()

    assert opening.type == "credit"
    assert opening.amount == Decimal("100.00")


# ---------------------------------------------------------------------------
# get_account_balance_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_account_balance_history(session: AsyncSession, test_user, test_workspace):
    """Balance history returns daily balance series."""
    account = await _make_account(session, test_user.id, "History Test")
    today = date.today()

    await _add_txn(session, test_user.id, account.id, 1000, "credit", today.replace(day=1), source="opening_balance")
    await _add_txn(session, test_user.id, account.id, 200, "debit", today.replace(day=min(5, today.day)))

    history = await get_account_balance_history(
        session, account.id, test_workspace.id,
        date_from=today.replace(day=1), date_to=today,
    )
    assert history is not None
    assert len(history) > 0
    # Each entry has date and balance
    assert "date" in history[0]
    assert "balance" in history[0]


@pytest.mark.asyncio
async def test_get_account_balance_history_not_found(session: AsyncSession, test_user, test_workspace):
    """Balance history for nonexistent account returns None."""
    result = await get_account_balance_history(session, uuid.uuid4(), test_workspace.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_account_balance_history_default_dates(session: AsyncSession, test_user, test_workspace):
    """Balance history uses current month if no dates provided."""
    account = await _make_account(session, test_user.id, "Default Dates")
    await _add_txn(session, test_user.id, account.id, 1000, "credit", date.today(), source="opening_balance")

    history = await get_account_balance_history(session, account.id, test_workspace.id)
    assert history is not None
    assert len(history) > 0


@pytest.mark.asyncio
async def test_get_account_balance_history_credit_card_negated(
    session: AsyncSession, test_user, test_workspace, test_connection,
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
        session, account.id, test_workspace.id,
        date_from=today, date_to=today,
    )
    assert history is not None
    assert len(history) == 1
    # CC with connection_id has sign=-1.0: a debit (spending) on CC
    # produces negative balance, negated to positive (showing debt increase)
    assert history[0]["balance"] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# SimpleFIN credit-card balance-sign normalization
# ---------------------------------------------------------------------------


async def _make_provider_connection(
    session: AsyncSession, user_id: uuid.UUID, provider: str,
) -> "uuid.UUID":
    from datetime import datetime, timezone
    from app.models.bank_connection import BankConnection
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user_id, provider=provider,
        external_id=f"ext-{provider}-{uuid.uuid4().hex[:8]}",
        institution_name=f"{provider} bank", credentials={"token": "fake"},
        status="active", last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn.id


def test_simplefin_to_internal_balance_flips_card():
    """SimpleFIN reports card debt as a negative number; normalize to positive."""
    assert _simplefin_to_internal_balance(
        "simplefin", "credit_card", Decimal("-500.00")
    ) == Decimal("500.00")


def test_simplefin_to_internal_balance_checking_unchanged():
    """A SimpleFIN non-card balance is already in the right convention."""
    assert _simplefin_to_internal_balance(
        "simplefin", "checking", Decimal("1500.00")
    ) == Decimal("1500.00")


def test_simplefin_to_internal_balance_other_provider_unchanged():
    """Pluggy/Enable already use positive-for-debt — never flip their cards."""
    assert _simplefin_to_internal_balance(
        "pluggy", "credit_card", Decimal("500.00")
    ) == Decimal("500.00")
    assert _simplefin_to_internal_balance(
        "enable_banking", "credit_card", Decimal("500.00")
    ) == Decimal("500.00")


@pytest.mark.asyncio
async def test_update_simplefin_account_to_credit_card_flips_balance(
    session: AsyncSession, test_user, test_workspace,
):
    """Decisive case: a SimpleFIN account stores debt as a negative balance under
    type="checking". When the user overrides the type to credit_card, the stored
    balance must flip to positive-for-debt so the downstream negation in
    serialize_account / dashboard yields -500 (owed) instead of +500 — without
    the flip the card double-counts in net worth (≈2x the debt)."""
    conn_id = await _make_provider_connection(session, test_user.id, "simplefin")
    account = await _make_account(
        session, test_user.id, "SimpleFIN Card", acc_type="checking",
        balance="-500.00", connection_id=conn_id, external_id="sf-card-1",
    )

    updated = await update_account(
        session, account.id, test_workspace.id, AccountUpdate(type="credit_card")
    )
    assert updated is not None
    assert updated.type == "credit_card"
    # Stored balance flips to positive-for-debt (matches Pluggy/Enable).
    assert updated.balance == Decimal("500.00")
    # Resolved/serialized balance is negative = the user owes 500.
    payload = serialize_account(updated, None, None)
    assert payload["current_balance"] == pytest.approx(-500.0)


@pytest.mark.asyncio
async def test_update_simplefin_account_away_from_credit_card_flips_back(
    session: AsyncSession, test_user, test_workspace,
):
    """Reverse direction: moving a SimpleFIN card back to a non-card type must
    flip the stored balance back to the raw provider sign so it stays
    consistent with a fresh sync (which writes the raw negative value)."""
    conn_id = await _make_provider_connection(session, test_user.id, "simplefin")
    account = await _make_account(
        session, test_user.id, "SimpleFIN WasCard", acc_type="credit_card",
        balance="500.00", connection_id=conn_id, external_id="sf-card-2",
    )

    updated = await update_account(
        session, account.id, test_workspace.id, AccountUpdate(type="checking")
    )
    assert updated is not None
    assert updated.type == "checking"
    assert updated.balance == Decimal("-500.00")


@pytest.mark.asyncio
async def test_update_pluggy_account_to_credit_card_does_not_flip(
    session: AsyncSession, test_user, test_workspace,
):
    """No double-count for non-SimpleFIN providers: a Pluggy account already
    uses positive-for-debt, so a type edit must NOT touch the stored balance."""
    conn_id = await _make_provider_connection(session, test_user.id, "pluggy")
    account = await _make_account(
        session, test_user.id, "Pluggy Card", acc_type="checking",
        balance="500.00", connection_id=conn_id, external_id="pl-card-1",
    )

    updated = await update_account(
        session, account.id, test_workspace.id, AccountUpdate(type="credit_card")
    )
    assert updated is not None
    assert updated.type == "credit_card"
    # Untouched — Pluggy was already positive-for-debt.
    assert updated.balance == Decimal("500.00")
    payload = serialize_account(updated, None, None)
    assert payload["current_balance"] == pytest.approx(-500.0)


@pytest.mark.asyncio
async def test_update_simplefin_type_change_not_crossing_card_keeps_balance(
    session: AsyncSession, test_user, test_workspace,
):
    """A SimpleFIN type edit that doesn't cross the credit_card boundary (e.g.
    checking → savings) must leave the stored balance alone."""
    conn_id = await _make_provider_connection(session, test_user.id, "simplefin")
    account = await _make_account(
        session, test_user.id, "SimpleFIN Savings", acc_type="checking",
        balance="1500.00", connection_id=conn_id, external_id="sf-sav-1",
    )

    updated = await update_account(
        session, account.id, test_workspace.id, AccountUpdate(type="savings")
    )
    assert updated is not None
    assert updated.type == "savings"
    assert updated.balance == Decimal("1500.00")


# ----- per-account institution resolution (issue #345) ------------------------


def _mem_connection(**overrides):
    from app.models.bank_connection import BankConnection

    defaults = dict(
        provider="simplefin",
        external_id="CON-1",
        institution_name="First Bank",
        logo_url="https://logos.example/first.png",
    )
    defaults.update(overrides)
    return BankConnection(**defaults)


def _mem_institution(name="Second Brokerage", logo_url="https://logos.example/second.png"):
    from app.models.institution import Institution

    return Institution(name=name, logo_url=logo_url)


def _mem_multi_connection(**overrides):
    """A connection spanning two institutions (multi-institution link)."""
    return _mem_connection(
        institutions=[_mem_institution("First Bank", None), _mem_institution()],
        **overrides,
    )


def test_institution_falls_back_to_connection_when_account_has_none():
    """Pluggy/Enable accounts have no institution row — connection wins."""
    from app.services.account_service import _institution

    acc = Account(name="Checking", type="checking")
    name, logo = _institution(acc, _mem_connection())
    assert name == "First Bank"
    assert logo == "https://logos.example/first.png"


def test_institution_prefers_the_accounts_own_on_multi_links():
    """On a link spanning several institutions, each account shows its own."""
    from app.services.account_service import _institution

    acc = Account(name="Brokerage", type="investment")
    acc.institution = _mem_institution()
    name, logo = _institution(acc, _mem_multi_connection())
    assert name == "Second Brokerage"
    assert logo == "https://logos.example/second.png"


def test_institution_never_mixes_name_and_logo_on_multi_links():
    """On a multi-institution link, an account whose institution has no logo
    must not borrow the connection's — that would pair one bank's name with
    another bank's icon."""
    from app.services.account_service import _institution

    acc = Account(name="Brokerage", type="investment")
    acc.institution = _mem_institution(logo_url=None)
    name, logo = _institution(acc, _mem_multi_connection())
    assert name == "Second Brokerage"
    assert logo is None


def test_institution_single_link_falls_back_to_connection_logo():
    """On a single-institution link the connection's logo is the same bank's,
    so it fills in when the institution row has none (review on #654)."""
    from app.services.account_service import _institution

    acc = Account(name="Checking", type="checking")
    acc.institution = _mem_institution("First Bank", logo_url=None)
    conn = _mem_connection(institutions=[acc.institution])
    name, logo = _institution(acc, conn)
    assert name == "First Bank"
    assert logo == "https://logos.example/first.png"


def test_institution_rename_wins_on_single_links_but_not_multi():
    """Renaming a single-bank link relabels its accounts (review on #654);
    renaming a multi-institution link labels the link, not the banks."""
    from app.services.account_service import _institution

    single = _mem_connection(display_name="My Bank")
    with_own = Account(name="Checking", type="checking")
    with_own.institution = _mem_institution("First Bank")
    single.institutions = [with_own.institution]
    assert _institution(with_own, single)[0] == "My Bank"

    multi = _mem_multi_connection(display_name="My Bank Link")
    at_second = Account(name="Brokerage", type="investment")
    at_second.institution = multi.institutions[1]
    assert _institution(at_second, multi)[0] == "Second Brokerage"

    hint_less = Account(name="Other", type="checking")
    assert _institution(hint_less, multi)[0] == "My Bank Link"


def test_institution_manual_account_has_none():
    from app.services.account_service import _institution

    assert _institution(Account(name="Wallet", type="checking"), None) == (None, None)


def _hint(name, logo=None, ext=None):
    from app.providers.base import AccountData

    return AccountData(
        external_id="x", name="A", type="checking",
        balance=Decimal("0"), currency="USD",
        institution_external_id=ext, institution_name=name, institution_logo_url=logo,
    )


@pytest.mark.asyncio
async def test_resolve_institution_matches_by_org_id_across_renames(
    session: AsyncSession, test_user,
):
    """A bank renamed on the provider side updates its row in place — same id,
    new name — instead of minting a new row (review on #654)."""
    from app.services.connection_service import _resolve_institution

    conn_id = await _make_provider_connection(session, test_user.id, "simplefin")

    first = await _resolve_institution(
        session, conn_id, {}, _hint("Chase Bank", "https://logos.example/old.png", ext="CON-1")
    )
    assert first is not None
    renamed = await _resolve_institution(
        session, conn_id, {}, _hint("Chase", "https://logos.example/new.png", ext="CON-1")
    )
    assert renamed is not None
    assert renamed.id == first.id
    assert renamed.name == "Chase"
    # The favicon-derived logo follows the provider too — a rename that
    # moves domains must not keep the old bank's icon.
    assert renamed.logo_url == "https://logos.example/new.png"


@pytest.mark.asyncio
async def test_resolve_institution_adopts_legacy_name_row(
    session: AsyncSession, test_user,
):
    """A row created before the server sent org ids is adopted by the first
    id-carrying hint with the same name, keeping its accounts attached."""
    from app.services.connection_service import _resolve_institution

    conn_id = await _make_provider_connection(session, test_user.id, "simplefin")

    legacy = await _resolve_institution(session, conn_id, {}, _hint("Chase Bank"))
    assert legacy is not None and legacy.external_id is None
    adopted = await _resolve_institution(session, conn_id, {}, _hint("Chase Bank", ext="CON-1"))
    assert adopted is not None
    assert adopted.id == legacy.id
    assert adopted.external_id == "CON-1"


@pytest.mark.asyncio
async def test_resolve_institution_same_name_different_orgs_stay_distinct(
    session: AsyncSession, test_user,
):
    """Two logins at the same bank (same display name, different org ids)
    must not collapse into one row."""
    from app.services.connection_service import _resolve_institution

    conn_id = await _make_provider_connection(session, test_user.id, "simplefin")

    one = await _resolve_institution(session, conn_id, {}, _hint("Fidelity", ext="CON-1"))
    two = await _resolve_institution(session, conn_id, {}, _hint("Fidelity", ext="CON-2"))
    assert one is not None and two is not None
    assert one.id != two.id


@pytest.mark.asyncio
async def test_resolve_institution_upserts_and_reuses(session: AsyncSession, test_user):
    """One row per identity; first sighting wins the logo, later sightings
    backfill a missing one; no hint → no row."""
    from app.services.connection_service import _resolve_institution

    conn_id = await _make_provider_connection(session, test_user.id, "simplefin")

    cache = {}
    first = await _resolve_institution(session, conn_id, cache, _hint("Chase Bank"))
    assert first is not None and first.logo_url is None
    again = await _resolve_institution(
        session, conn_id, {}, _hint("Chase Bank", "https://logos.example/chase.png")
    )
    assert again is not None
    assert again.id == first.id  # reused across cache misses
    assert again.logo_url == "https://logos.example/chase.png"  # backfilled
    assert await _resolve_institution(session, conn_id, cache, _hint(None)) is None
    assert await _resolve_institution(session, conn_id, cache, _hint("   ")) is None


def test_clean_logo_url_drops_overlong_urls():
    """A URL longer than the column is dropped, not truncated — a truncated
    URL is a broken URL (review on #654)."""
    from app.services.connection_service import _clean_logo_url

    assert _clean_logo_url("https://ok.example/logo.png") == "https://ok.example/logo.png"
    assert _clean_logo_url("https://long.example/" + "a" * 500) is None
    assert _clean_logo_url("   ") is None
    assert _clean_logo_url(None) is None
