"""Tests for recurring-bill ↔ transaction matching (issue #116)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.services import recurring_match_service as rms
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    generate_pending,
)


@pytest_asyncio.fixture
async def account(session: AsyncSession, test_user, test_workspace) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="MatchAcc",
        type="checking",
        balance=Decimal("10000"),
        currency="BRL",
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _make_bill(session, test_workspace, test_user, account, **overrides):
    data = RecurringTransactionCreate(
        description=overrides.pop("description", "Netflix Subscription"),
        amount=overrides.pop("amount", Decimal("39.90")),
        currency=overrides.pop("currency", "BRL"),
        type=overrides.pop("type", "debit"),
        frequency=overrides.pop("frequency", "monthly"),
        start_date=overrides.pop("start_date", date(2025, 1, 10)),
        account_id=account.id,
        **overrides,
    )
    return await create_recurring_transaction(session, test_workspace.id, test_user.id, data)


async def _add_tx(session, test_user, test_workspace, account, **kw):
    tx = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account.id,
        description=kw.get("description", "NETFLIX SUBSCRIPTION"),
        amount=kw.get("amount", Decimal("39.90")),
        currency="BRL",
        date=kw["date"],
        type=kw.get("type", "debit"),
        source=kw.get("source", "sync"),
        status=kw.get("status", "posted"),
        external_id=kw.get("external_id", "ext-1"),
        recurring_transaction_id=kw.get("recurring_transaction_id"),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_description_similarity():
    assert rms._description_similarity("Netflix Sub", "netflix sub") == 1.0
    assert rms._description_similarity("Netflix", "Spotify") == 0.0
    assert rms._description_similarity(None, "x") == 0.0


def test_match_window():
    assert rms._match_window("weekly") == (2, 2)
    assert rms._match_window("monthly") == (3, 5)
    assert rms._match_window("yearly") == (3, 5)


# ---------------------------------------------------------------------------
# find_bill_for_incoming (sync-first path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_bill_matches_within_window(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    # next_occurrence = 2025-01-10; charge posts 2 days late.
    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 12), "NETFLIX SUBSCRIPTION",
    )
    assert match is not None
    assert match.id == bill.id


@pytest.mark.asyncio
async def test_find_bill_rejects_outside_window(session, test_user, test_workspace, account):
    await _make_bill(session, test_workspace, test_user, account)
    # 10 days after the due date is well outside the monthly window.
    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 20), "NETFLIX SUBSCRIPTION",
    )
    assert match is None


@pytest.mark.asyncio
async def test_find_bill_rejects_amount_and_type_mismatch(session, test_user, test_workspace, account):
    await _make_bill(session, test_workspace, test_user, account)
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("41.00"), "BRL", "debit",
        date(2025, 1, 10), "NETFLIX SUBSCRIPTION",
    ) is None
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "credit",
        date(2025, 1, 10), "NETFLIX SUBSCRIPTION",
    ) is None


@pytest.mark.asyncio
async def test_find_bill_rejects_currency_mismatch(session, test_user, test_workspace, account):
    # Bill is in BRL; a same-numeric charge in another currency must not match.
    await _make_bill(session, test_workspace, test_user, account, currency="BRL")
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "USD", "debit",
        date(2025, 1, 10), "NETFLIX SUBSCRIPTION",
    ) is None


@pytest.mark.asyncio
async def test_find_bill_rejects_low_similarity(session, test_user, test_workspace, account):
    await _make_bill(session, test_workspace, test_user, account)
    match = await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 10), "Amazon Prime",
    )
    assert match is None


# ---------------------------------------------------------------------------
# find_placeholder_for_incoming (placeholder-first path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_placeholder_matches_generated_row(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    placeholder = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="recurring", external_id=None,
        recurring_transaction_id=bill.id, description="Netflix Subscription",
    )
    found = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 11), "NETFLIX SUBSCRIPTION",
    )
    assert found is not None and found.id == placeholder.id


@pytest.mark.asyncio
async def test_find_placeholder_ignores_already_linked_synced(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    # A real synced row (has external_id) is not a placeholder to merge into.
    await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="sync", external_id="ext-9",
        recurring_transaction_id=bill.id,
    )
    found = await rms.find_placeholder_for_incoming(
        session, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 10), "NETFLIX SUBSCRIPTION",
    )
    assert found is None


# ---------------------------------------------------------------------------
# advance_past
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_past_moves_pointer(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    assert bill.next_occurrence == date(2025, 1, 10)
    rms.advance_past(bill, date(2025, 1, 12))
    assert bill.next_occurrence == date(2025, 2, 10)


@pytest.mark.asyncio
async def test_advance_past_deactivates_past_end_date(session, test_user, test_workspace, account):
    bill = await _make_bill(
        session, test_workspace, test_user, account, end_date=date(2025, 1, 31),
    )
    rms.advance_past(bill, date(2025, 1, 10))
    assert bill.next_occurrence == date(2025, 2, 10)
    assert bill.is_active is False


@pytest.mark.asyncio
async def test_advance_past_early_charge_still_advances(session, test_user, test_workspace, account):
    """An early-posted charge (inside the before-window, before next_occurrence)
    must still advance the pointer, or generate_pending would duplicate it."""
    bill = await _make_bill(session, test_workspace, test_user, account)  # next_occ 2025-01-10
    rms.advance_past(bill, date(2025, 1, 8))  # 2 days early
    assert bill.next_occurrence == date(2025, 2, 10)


# ---------------------------------------------------------------------------
# generate_pending integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_pending_links_existing_real_tx(session, test_user, test_workspace, account):
    """A synced charge that already covers the occurrence is linked, not duplicated."""
    bill = await _make_bill(session, test_workspace, test_user, account)
    real = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 11), source="sync", external_id="bank-1",
    )

    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))

    # No placeholder was written for January; the real charge got linked.
    assert count == 0
    await session.refresh(real)
    assert real.recurring_transaction_id == bill.id

    result = await session.execute(
        select(Transaction).where(Transaction.account_id == account.id)
    )
    assert len(result.scalars().all()) == 1  # only the synced row, no duplicate


@pytest.mark.asyncio
async def test_generate_pending_stamps_placeholder_link(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))
    assert count == 1
    result = await session.execute(
        select(Transaction).where(Transaction.source == "recurring")
    )
    tx = result.scalar_one()
    assert tx.recurring_transaction_id == bill.id


@pytest.mark.asyncio
async def test_generate_pending_skips_auto_generate_off(session, test_user, test_workspace, account):
    await _make_bill(session, test_workspace, test_user, account, auto_generate=False)
    count = await generate_pending(session, test_user.id, up_to=date(2025, 6, 1))
    assert count == 0
    result = await session.execute(
        select(Transaction).where(Transaction.account_id == account.id)
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_generate_pending_description_mismatch_creates_placeholder(
    session, test_user, test_workspace, account
):
    """A real charge with the same amount/type/date but an unrelated description
    must NOT be swallowed as the bill's occurrence; a placeholder is written and
    the real charge is left untouched."""
    bill = await _make_bill(session, test_workspace, test_user, account)
    unrelated = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="sync", external_id="bank-x",
        description="Amazon Marketplace",
    )
    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))
    assert count == 1  # placeholder created, real charge not reused
    await session.refresh(unrelated)
    assert unrelated.recurring_transaction_id is None
    result = await session.execute(
        select(Transaction).where(Transaction.source == "recurring")
    )
    ph = result.scalar_one()
    assert ph.recurring_transaction_id == bill.id


@pytest.mark.asyncio
async def test_find_bill_weekly_window_boundary(session, test_user, test_workspace, account):
    await _make_bill(
        session, test_workspace, test_user, account,
        description="Weekly Gym", frequency="weekly", start_date=date(2025, 1, 6),
    )
    # weekly window is (2, 2): 2 days late matches, 3 days late does not.
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 8), "Weekly Gym",
    ) is not None
    assert await rms.find_bill_for_incoming(
        session, test_user.id, account.id, Decimal("39.90"), "BRL", "debit",
        date(2025, 1, 9), "Weekly Gym",
    ) is None


@pytest.mark.asyncio
async def test_find_real_tx_ignores_already_linked(session, test_user, test_workspace, account):
    bill = await _make_bill(session, test_workspace, test_user, account)
    await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="sync", external_id="bank-1",
        recurring_transaction_id=bill.id,  # already linked
    )
    found = await rms.find_real_tx_for_occurrence(session, bill, date(2025, 1, 10))
    assert found is None


# ---------------------------------------------------------------------------
# Unlink escape hatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlink_recurring_clears_link(session, test_user, test_workspace, account):
    from app.services import transaction_service

    bill = await _make_bill(session, test_workspace, test_user, account)
    tx = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="sync", external_id="bank-1",
        recurring_transaction_id=bill.id,
    )
    result = await transaction_service.unlink_recurring_transaction(
        session, tx.id, test_workspace.id
    )
    assert result is not None
    assert result.recurring_transaction_id is None
    await session.refresh(tx)
    assert tx.recurring_transaction_id is None


@pytest.mark.asyncio
async def test_unlink_recurring_noop_when_not_linked(session, test_user, test_workspace, account):
    from app.services import transaction_service

    tx = await _add_tx(
        session, test_user, test_workspace, account,
        date=date(2025, 1, 10), source="sync", external_id="bank-2",
    )
    result = await transaction_service.unlink_recurring_transaction(
        session, tx.id, test_workspace.id
    )
    assert result is None  # nothing to unlink → 404 at the API layer
