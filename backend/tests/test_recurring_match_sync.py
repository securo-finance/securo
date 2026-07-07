"""Deep integration tests for recurring-bill matching through the real
bank-sync ingestion path (issue #116).

These drive `sync_connection` end-to-end with a mocked provider, so they
exercise the actual incremental-loop wiring (placeholder merge, bill
stamp+advance, no-match passthrough, and the greedy one-per-occurrence
behaviour within a single sync batch), not just the matcher helpers.

Note: `sync_connection` commits (expire_on_commit), so ORM objects created
before the sync are expired afterwards. We capture primitive ids up front and
re-fetch rows after the sync rather than touching expired attributes (which
would trigger a synchronous lazy-load).
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.services.connection_service import sync_connection
from app.services.recurring_transaction_service import create_recurring_transaction


@pytest_asyncio.fixture
async def conn_account(session: AsyncSession, test_user, test_workspace):
    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Sync Bank", credentials={"token": "fake"},
        status="active", last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        workspace_id=test_workspace.id, name="Checking", type="checking",
        external_id="acc-ext-1", balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(conn)
    await session.refresh(account)
    return conn, account


def _provider(transactions, account_ext="acc-ext-1"):
    from app.providers.base import AccountData
    p = AsyncMock()
    p.refresh_credentials = AsyncMock(return_value={"token": "t"})
    p.get_accounts = AsyncMock(return_value=[
        AccountData(external_id=account_ext, name="Checking",
                    type="checking", balance=Decimal("0"), currency="BRL"),
    ])
    p.get_transactions = AsyncMock(return_value=transactions)
    return p


def _tx(**kw):
    from app.providers.base import TransactionData
    kw.setdefault("currency", "BRL")
    kw.setdefault("type", "debit")
    kw.setdefault("status", "posted")
    return TransactionData(**kw)


async def _run_sync(session, conn_id, test_workspace, test_user, provider):
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        return await sync_connection(session, conn_id, test_workspace.id, test_user.id)


async def _make_bill(session, test_workspace, test_user, account, **ov):
    data = RecurringTransactionCreate(
        description=ov.pop("description", "Netflix Subscription"),
        amount=ov.pop("amount", Decimal("39.90")),
        currency=ov.pop("currency", "BRL"),
        type=ov.pop("type", "debit"),
        frequency=ov.pop("frequency", "monthly"),
        start_date=ov.pop("start_date", date(2025, 1, 10)),
        account_id=account.id, **ov,
    )
    return await create_recurring_transaction(session, test_workspace.id, test_user.id, data)


async def _all_txs(session, account_id):
    # Exclude the synthetic opening-balance row the sync writes to reconcile
    # the provider-reported balance; we only care about real/recurring rows.
    r = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source != "opening_balance",
        )
    )
    return list(r.scalars().all())


# ---------------------------------------------------------------------------
# Sync-first: incoming charge stamps the bill and advances it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_links_and_advances_bill(session, test_user, test_workspace, conn_account):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account,
                            start_date=date(2025, 1, 10))
    bill_id = bill.id
    provider = _provider([_tx(external_id="s1", description="NETFLIX SUBSCRIPTION",
                              amount=Decimal("39.90"), date=date(2025, 1, 12))])

    await _run_sync(session, conn_id, test_workspace, test_user, provider)

    txs = await _all_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].recurring_transaction_id == bill_id
    assert txs[0].source == "sync"
    refreshed = await session.get(RecurringTransaction, bill_id)
    assert refreshed.next_occurrence == date(2025, 2, 10)  # advanced past fulfilled occ


# ---------------------------------------------------------------------------
# Placeholder-first: incoming charge merges into the generated row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_merges_into_placeholder(session, test_user, test_workspace, conn_account):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account,
                            start_date=date(2025, 1, 10))
    bill_id = bill.id
    # Placeholder as generate_pending would have written it (occurrence already
    # advanced next_occurrence to Feb).
    bill.next_occurrence = date(2025, 2, 10)
    placeholder = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account_id,
        workspace_id=test_workspace.id, description="Netflix Subscription",
        amount=Decimal("39.90"), currency="BRL", date=date(2025, 1, 10),
        type="debit", source="recurring", status="posted",
        recurring_transaction_id=bill_id, created_at=datetime.now(timezone.utc),
    )
    session.add(placeholder)
    await session.commit()
    placeholder_id = placeholder.id

    provider = _provider([_tx(external_id="s1", description="NETFLIX SUBSCRIPTION",
                              amount=Decimal("39.90"), date=date(2025, 1, 11))])
    await _run_sync(session, conn_id, test_workspace, test_user, provider)

    txs = await _all_txs(session, account_id)
    assert len(txs) == 1  # merged, not duplicated
    merged = txs[0]
    assert merged.id == placeholder_id
    assert merged.external_id == "s1"
    assert merged.source == "sync"
    assert merged.recurring_transaction_id == bill_id
    refreshed = await session.get(RecurringTransaction, bill_id)
    assert refreshed.next_occurrence == date(2025, 2, 10)  # NOT advanced again


# ---------------------------------------------------------------------------
# Non-matching charge is left independent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_early_charge_advances_and_no_duplicate(
    session, test_user, test_workspace, conn_account
):
    """A charge that posts before the expected occurrence links, advances the
    bill, and does NOT leave an opening for generate_pending to duplicate it."""
    from app.services.recurring_transaction_service import generate_pending

    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account,
                            start_date=date(2025, 1, 10))
    bill_id = bill.id
    # Posts 2 days early, inside the before-window of the Jan 10 occurrence.
    provider = _provider([_tx(external_id="s1", description="NETFLIX SUBSCRIPTION",
                              amount=Decimal("39.90"), date=date(2025, 1, 8))])
    await _run_sync(session, conn_id, test_workspace, test_user, provider)

    refreshed = await session.get(RecurringTransaction, bill_id)
    assert refreshed.next_occurrence == date(2025, 2, 10)  # advanced despite early posting

    # generate_pending past the Jan 10 occurrence must not re-create it.
    await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))
    txs = await _all_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].recurring_transaction_id == bill_id


@pytest.mark.asyncio
async def test_sync_amount_mismatch_not_linked(session, test_user, test_workspace, conn_account):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account,
                            amount=Decimal("39.90"), start_date=date(2025, 1, 10))
    bill_id = bill.id
    provider = _provider([_tx(external_id="s1", description="NETFLIX SUBSCRIPTION",
                              amount=Decimal("50.00"), date=date(2025, 1, 10))])
    await _run_sync(session, conn_id, test_workspace, test_user, provider)

    txs = await _all_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].recurring_transaction_id is None
    refreshed = await session.get(RecurringTransaction, bill_id)
    assert refreshed.next_occurrence == date(2025, 1, 10)  # untouched


# ---------------------------------------------------------------------------
# Greedy one-per-occurrence: two matching charges in one batch link only once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_two_charges_same_occurrence_links_one(
    session, test_user, test_workspace, conn_account
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account,
                            amount=Decimal("39.90"), start_date=date(2025, 1, 10))
    bill_id = bill.id
    provider = _provider([
        _tx(external_id="s1", description="NETFLIX SUBSCRIPTION",
            amount=Decimal("39.90"), date=date(2025, 1, 10)),
        _tx(external_id="s2", description="NETFLIX SUBSCRIPTION",
            amount=Decimal("39.90"), date=date(2025, 1, 11)),
    ])
    await _run_sync(session, conn_id, test_workspace, test_user, provider)

    txs = await _all_txs(session, account_id)
    assert len(txs) == 2  # both land, no collapse
    linked = [t for t in txs if t.recurring_transaction_id == bill_id]
    assert len(linked) == 1  # exactly one occurrence fulfilled
    refreshed = await session.get(RecurringTransaction, bill_id)
    assert refreshed.next_occurrence == date(2025, 2, 10)  # advanced exactly once


# ---------------------------------------------------------------------------
# Re-sync of an already-linked charge is idempotent (external_id pass 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resync_is_idempotent(session, test_user, test_workspace, conn_account):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account,
                            amount=Decimal("39.90"), start_date=date(2025, 1, 10))
    bill_id = bill.id

    def txns():
        return [_tx(external_id="s1", description="NETFLIX SUBSCRIPTION",
                    amount=Decimal("39.90"), date=date(2025, 1, 10))]

    await _run_sync(session, conn_id, test_workspace, test_user, _provider(txns()))
    await _run_sync(session, conn_id, test_workspace, test_user, _provider(txns()))

    txs = await _all_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].recurring_transaction_id == bill_id
    refreshed = await session.get(RecurringTransaction, bill_id)
    assert refreshed.next_occurrence == date(2025, 2, 10)  # advanced once, not twice
