"""A bill kept in a foreign currency, charged in the account's own (#1073).

A USD subscription on a BRL card is charged in BRL at that month's rate, so
the charge never has the bill's amount or currency. It is recognised by the
account, the direction, the window and the description, and only when a
single charge fits.
"""
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.schemas.transaction import TransactionImport
from app.services import reconciliation_policy, reconciliation_rule_service
from app.services import recurring_match_service as rms
from app.services.import_service import import_transactions
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    generate_pending,
)

DUE = date(2025, 1, 10)


@pytest_asyncio.fixture
async def card(session: AsyncSession, test_user, test_workspace) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Card",
        type="credit_card",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _usd_bill(session, test_workspace, test_user, card, description="Claude"):
    data = RecurringTransactionCreate(
        description=description,
        amount=Decimal("22.22"),
        currency="USD",
        type="debit",
        frequency="monthly",
        start_date=DUE,
        account_id=card.id,
    )
    return await create_recurring_transaction(session, test_workspace.id, test_user.id, data)


async def _brl_charge(session, test_user, test_workspace, card, **kw):
    tx = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=card.id,
        description=kw.get("description", "Claude"),
        amount=kw.get("amount", Decimal("121.37")),
        currency="BRL",
        date=kw.get("date", DUE),
        type="debit",
        source=kw.get("source", "import"),
        status="posted",
        external_id=kw.get("external_id", "bank-1"),
        recurring_transaction_id=kw.get("recurring_transaction_id"),
    )
    session.add(tx)
    await session.commit()
    await session.refresh(tx)
    return tx


async def _rows(session, card):
    result = await session.execute(select(Transaction).where(Transaction.account_id == card.id))
    return result.scalars().all()


@pytest.mark.asyncio
async def test_generate_pending_links_the_local_charge_instead_of_duplicating(
    session, test_user, test_workspace, card
):
    bill = await _usd_bill(session, test_workspace, test_user, card)
    charge = await _brl_charge(session, test_user, test_workspace, card)

    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))

    assert count == 0
    await session.refresh(charge)
    assert charge.recurring_transaction_id == bill.id
    assert [row.id for row in await _rows(session, card)] == [charge.id]


@pytest.mark.asyncio
async def test_generate_pending_does_not_guess_between_two_local_charges(
    session, test_user, test_workspace, card
):
    await _usd_bill(session, test_workspace, test_user, card)
    first = await _brl_charge(session, test_user, test_workspace, card, external_id="bank-1")
    second = await _brl_charge(
        session, test_user, test_workspace, card, external_id="bank-2", date=date(2025, 1, 11)
    )

    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))

    assert count == 1
    for charge in (first, second):
        await session.refresh(charge)
        assert charge.recurring_transaction_id is None


@pytest.mark.asyncio
async def test_generate_pending_still_writes_the_bill_for_an_unrelated_charge(
    session, test_user, test_workspace, card
):
    await _usd_bill(session, test_workspace, test_user, card)
    charge = await _brl_charge(session, test_user, test_workspace, card, description="Padaria")

    count = await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))

    assert count == 1
    await session.refresh(charge)
    assert charge.recurring_transaction_id is None


@pytest.mark.asyncio
async def test_a_bill_in_the_account_currency_is_not_matched_on_description_alone(
    session, test_user, test_workspace, card
):
    data = RecurringTransactionCreate(
        description="Claude", amount=Decimal("100.00"), currency="BRL", type="debit",
        frequency="monthly", start_date=DUE, account_id=card.id,
    )
    bill = await create_recurring_transaction(session, test_workspace.id, test_user.id, data)
    await _brl_charge(session, test_user, test_workspace, card)

    assert await rms.find_real_tx_for_occurrence(session, bill, DUE) is None


@pytest.mark.asyncio
async def test_find_placeholder_takes_a_foreign_placeholder_for_a_local_charge(
    session, test_user, test_workspace, card
):
    bill = await _usd_bill(session, test_workspace, test_user, card)
    await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))
    (placeholder,) = await _rows(session, card)
    assert placeholder.recurring_transaction_id == bill.id

    found = await rms.find_placeholder_for_incoming(
        session, card.id, Decimal("121.37"), "BRL", "debit", DUE, "Claude"
    )

    assert found is not None and found.id == placeholder.id


@pytest.mark.asyncio
async def test_import_turns_the_foreign_placeholder_into_the_local_charge(
    session, test_user, test_workspace, card
):
    bill = await _usd_bill(session, test_workspace, test_user, card)
    await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))

    imported, skipped, _, _ = await import_transactions(
        session, test_workspace.id, test_user.id, card.id,
        [TransactionImport(
            description="Claude", external_id="statement-1", amount=Decimal("121.37"),
            currency="BRL", date=DUE, type="debit",
        )],
        "ofx", detected_format="ofx",
    )

    assert (imported, skipped) == (1, 0)
    (row,) = await _rows(session, card)
    assert row.recurring_transaction_id == bill.id
    assert row.currency == "BRL"
    assert row.amount == Decimal("121.37")
    assert row.external_id == "statement-1"


@pytest.mark.asyncio
async def test_a_disabled_rule_links_nothing_across_currencies(
    session, test_user, test_workspace, card
):
    """Off means off: the rate-blind pass still answers to the rule."""
    await reconciliation_rule_service.upsert_override(
        session,
        test_workspace.id,
        test_user.id,
        reconciliation_policy.MATCH_RECURRING["node"],
        "same_account_exact",
        {"enabled": False},
    )
    bill = await _usd_bill(session, test_workspace, test_user, card)
    charge = await _brl_charge(session, test_user, test_workspace, card)

    assert await rms.find_real_tx_for_occurrence(session, bill, DUE) is None
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 20)) == 1

    await session.refresh(charge)
    assert charge.recurring_transaction_id is None
    assert (
        await rms.find_placeholder_for_incoming(
            session, card.id, Decimal("121.37"), "BRL", "debit", DUE, "Claude"
        )
        is None
    )


@pytest.mark.asyncio
async def test_import_clears_a_stamp_made_in_the_bills_currency(
    session, test_user, test_workspace, card
):
    """The row takes the charge's currency, so a stamp made from the bill's
    cannot survive it (#1073)."""
    bill = await _usd_bill(session, test_workspace, test_user, card)
    placeholder = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=card.id,
        description="Claude",
        amount=Decimal("22.22"),
        currency="USD",
        date=DUE,
        amount_primary=Decimal("121.37"),
        fx_rate_used=Decimal("5.4613"),
        type="debit",
        source="recurring",
        status="posted",
        recurring_transaction_id=bill.id,
    )
    session.add(placeholder)
    await session.commit()

    stamp = AsyncMock()
    with patch("app.services.import_service.stamp_primary_amount", new=stamp):
        await import_transactions(
            session, test_workspace.id, test_user.id, card.id,
            [TransactionImport(
                description="Claude", external_id="statement-1",
                amount=Decimal("121.37"), currency="BRL", date=DUE, type="debit",
            )],
            "ofx", detected_format="ofx",
        )

    (row,) = await _rows(session, card)
    assert row.id == placeholder.id
    assert row.currency == "BRL"
    assert row.amount_primary is None
    assert row.fx_rate_used is None
    stamp.assert_awaited()
