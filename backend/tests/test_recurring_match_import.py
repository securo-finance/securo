"""Integration tests for recurring-bill matching through the CSV/OFX import
path (issue #116)."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.schemas.transaction import TransactionImport
from app.services.import_service import import_transactions
from app.services.recurring_transaction_service import create_recurring_transaction


async def _make_bill(session, test_workspace, test_user, account, **ov):
    data = RecurringTransactionCreate(
        description=ov.pop("description", "Netflix Subscription"),
        amount=ov.pop("amount", Decimal("39.90")),
        currency=ov.pop("currency", "BRL"),
        type=ov.pop("type", "debit"),
        frequency=ov.pop("frequency", "monthly"),
        start_date=ov.pop("start_date", date(2026, 1, 10)),
        account_id=account.id, **ov,
    )
    return await create_recurring_transaction(session, test_workspace.id, test_user.id, data)


async def _run_import(session, test_workspace, test_user, account_id, txns, source="csv"):
    with patch("app.services.import_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.import_service.apply_rules_to_transaction", new_callable=AsyncMock):
        return await import_transactions(
            session, test_workspace.id, test_user.id, account_id, txns, source,
        )


async def _real_txs(session, account_id):
    r = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source != "opening_balance",
        )
    )
    return list(r.scalars().all())


@pytest.mark.asyncio
async def test_import_links_and_advances_bill(session, test_user, test_workspace, test_account):
    account_id = test_account.id
    bill = await _make_bill(session, test_workspace, test_user, test_account,
                            amount=Decimal("39.90"), start_date=date(2026, 1, 10))
    bill_id = bill.id
    txns = [TransactionImport(description="NETFLIX SUBSCRIPTION", amount=Decimal("39.90"),
                              date=date(2026, 1, 12), type="debit", currency="BRL")]

    imported, skipped, _, _ = await _run_import(session, test_workspace, test_user, account_id, txns)

    assert imported == 1 and skipped == 0
    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].recurring_transaction_id == bill_id
    refreshed = await session.get(RecurringTransaction, bill_id)
    assert refreshed.next_occurrence == date(2026, 2, 10)


@pytest.mark.asyncio
async def test_import_merges_into_placeholder(session, test_user, test_workspace, test_account):
    account_id = test_account.id
    bill = await _make_bill(session, test_workspace, test_user, test_account,
                            amount=Decimal("39.90"), start_date=date(2026, 1, 10))
    bill_id = bill.id
    bill.next_occurrence = date(2026, 2, 10)
    placeholder = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account_id,
        workspace_id=test_workspace.id, description="Netflix Subscription",
        amount=Decimal("39.90"), currency="BRL", date=date(2026, 1, 10),
        type="debit", source="recurring", status="posted",
        recurring_transaction_id=bill_id, created_at=datetime.now(timezone.utc),
    )
    session.add(placeholder)
    await session.commit()
    placeholder_id = placeholder.id

    # Different case bypasses the exact-match field dedup, so the recurring
    # merge (case-insensitive similarity) is what catches it.
    txns = [TransactionImport(description="NETFLIX SUBSCRIPTION", amount=Decimal("39.90"),
                              date=date(2026, 1, 11), type="debit", currency="BRL")]
    imported, skipped, _, _ = await _run_import(session, test_workspace, test_user, account_id, txns)

    assert imported == 1
    txs = await _real_txs(session, account_id)
    assert len(txs) == 1  # merged into placeholder, no duplicate
    assert txs[0].id == placeholder_id
    assert txs[0].source == "csv"
    assert txs[0].recurring_transaction_id == bill_id


@pytest.mark.asyncio
async def test_import_amount_mismatch_not_linked(session, test_user, test_workspace, test_account):
    account_id = test_account.id
    bill = await _make_bill(session, test_workspace, test_user, test_account,
                            amount=Decimal("39.90"), start_date=date(2026, 1, 10))
    bill_id = bill.id
    txns = [TransactionImport(description="NETFLIX SUBSCRIPTION", amount=Decimal("99.00"),
                              date=date(2026, 1, 10), type="debit", currency="BRL")]
    await _run_import(session, test_workspace, test_user, account_id, txns)

    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].recurring_transaction_id is None
    refreshed = await session.get(RecurringTransaction, bill_id)
    assert refreshed.next_occurrence == date(2026, 1, 10)
