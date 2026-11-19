"""Rules run on the occurrences a recurring schedule generates (issue #568).

A generated occurrence is a new row like a manual, imported or synced one, so
the workspace's active rules read it once when it is written. The template's
own category is the user's choice and outranks a category rule; an `ignore`
on a placeholder still waiting for its bank charge waits for that charge.
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
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.models.workspace import Workspace
from app.providers.base import AccountData, TransactionData
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.schemas.rule import RuleAction, RuleCondition, RuleCreate
from app.services.connection_service import sync_connection
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    generate_pending,
)
from app.services.rule_service import create_rule


@pytest_asyncio.fixture
async def manual_account(session: AsyncSession, test_user, test_workspace):
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
        name="Wallet", type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.commit()
    return account


@pytest_asyncio.fixture
async def synced_account(session: AsyncSession, test_user, test_workspace):
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
        external_id="acc-ext-568", balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.commit()
    return conn, account


async def _make_bill(session, workspace, user, account, **overrides):
    data = RecurringTransactionCreate(
        description=overrides.pop("description", "Gym membership"),
        amount=overrides.pop("amount", Decimal("99.90")),
        currency="BRL",
        type="debit",
        frequency="monthly",
        start_date=date(2025, 1, 10),
        account_id=account.id,
        **overrides,
    )
    return await create_recurring_transaction(session, workspace.id, user.id, data)


async def _rule(session, workspace_id, user_id, actions, contains="Gym"):
    await create_rule(
        session,
        workspace_id,
        user_id,
        RuleCreate(
            name=f"Rule {uuid.uuid4().hex[:6]}",
            conditions=[RuleCondition(field="description", op="contains", value=contains)],
            actions=actions,
            apply_to_existing=False,
        ),
    )


async def _generated(session, account_id):
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source == "recurring",
        )
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_generated_occurrence_takes_payee_and_notes_from_rules(
    session, test_user, test_workspace, manual_account
):
    payee = Payee(user_id=test_user.id, workspace_id=test_workspace.id, name="Smart Fit")
    session.add(payee)
    await session.flush()
    payee_id = payee.id
    await _make_bill(session, test_workspace, test_user, manual_account)
    await _rule(
        session, test_workspace.id, test_user.id,
        [
            RuleAction(op="set_payee", value=str(payee_id)),
            RuleAction(op="append_notes", value="#health"),
        ],
    )

    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 1

    [occurrence] = await _generated(session, manual_account.id)
    assert occurrence.payee_id == payee_id
    assert occurrence.notes == "#health"


@pytest.mark.asyncio
async def test_rules_do_not_replace_the_templates_category(
    session, test_user, test_workspace, manual_account, test_categories
):
    template_category, rule_category = test_categories[0], test_categories[1]
    await _make_bill(
        session, test_workspace, test_user, manual_account,
        category_id=template_category.id,
    )
    await _rule(
        session, test_workspace.id, test_user.id,
        [
            RuleAction(op="set_category", value=str(rule_category.id)),
            RuleAction(op="append_notes", value="#health"),
        ],
    )

    await generate_pending(session, test_user.id, up_to=date(2025, 1, 10))

    [occurrence] = await _generated(session, manual_account.id)
    assert occurrence.category_id == template_category.id
    assert occurrence.notes == "#health"


@pytest.mark.asyncio
async def test_uncategorized_template_takes_the_rules_category(
    session, test_user, test_workspace, manual_account, test_categories
):
    await _make_bill(session, test_workspace, test_user, manual_account)
    await _rule(
        session, test_workspace.id, test_user.id,
        [RuleAction(op="set_category", value=str(test_categories[1].id))],
    )

    await generate_pending(session, test_user.id, up_to=date(2025, 1, 10))

    [occurrence] = await _generated(session, manual_account.id)
    assert occurrence.category_id == test_categories[1].id


@pytest.mark.asyncio
async def test_rules_of_another_workspace_do_not_apply(
    session, test_user, test_workspace, manual_account
):
    other = Workspace(id=uuid.uuid4(), name="Other")
    session.add(other)
    await session.commit()
    await _make_bill(session, test_workspace, test_user, manual_account)
    await _rule(
        session, other.id, test_user.id,
        [RuleAction(op="append_notes", value="#elsewhere")],
    )

    await generate_pending(session, test_user.id, up_to=date(2025, 1, 10))

    [occurrence] = await _generated(session, manual_account.id)
    assert occurrence.notes is None


@pytest.mark.asyncio
async def test_ignore_rule_applies_to_a_posted_occurrence(
    session, test_user, test_workspace, manual_account
):
    await _make_bill(session, test_workspace, test_user, manual_account)
    await _rule(session, test_workspace.id, test_user.id, [RuleAction(op="ignore", value=True)])

    await generate_pending(session, test_user.id, up_to=date(2025, 1, 10))

    [occurrence] = await _generated(session, manual_account.id)
    assert occurrence.status == "posted"
    assert occurrence.is_ignored is True


def _bank_reporting_the_charge(description: str = "Gym membership") -> AsyncMock:
    """The provider stub: one account, one charge, under the wording it wants."""
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_accounts = AsyncMock(return_value=[
        AccountData(external_id="acc-ext-568", name="Checking", type="checking",
                    balance=Decimal("0"), currency="BRL"),
    ])
    provider.get_transactions = AsyncMock(return_value=[
        TransactionData(
            external_id="gym-jan", description=description,
            amount=Decimal("99.90"), date=date(2025, 1, 11),
            type="debit", status="posted", currency="BRL",
        )
    ])
    return provider


@pytest.mark.asyncio
async def test_ignore_rule_waits_for_the_bank_charge_on_a_pending_placeholder(
    session, test_user, test_workspace, synced_account
):
    """An ignored placeholder is never matched, so the charge would land beside it.

    The placeholder stays matchable while pending; the sync folds the
    incoming charge into it and carries the rule's `ignore` over then.
    """
    conn, account = synced_account
    conn_id, account_id = conn.id, account.id
    await _make_bill(session, test_workspace, test_user, account)
    await _rule(
        session, test_workspace.id, test_user.id,
        [RuleAction(op="ignore", value=True), RuleAction(op="append_notes", value="#health")],
    )

    await generate_pending(session, test_user.id, up_to=date(2025, 1, 10))

    [placeholder] = await _generated(session, account_id)
    placeholder_id = placeholder.id
    assert placeholder.status == "pending"
    assert placeholder.is_ignored is False
    assert placeholder.notes == "#health"

    provider = _bank_reporting_the_charge()
    with patch("app.services.connection_service.get_provider", return_value=provider), \
            patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
            patch("app.services.connection_service.stamp_primary_amount", new=AsyncMock()):
        await sync_connection(session, conn_id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source != "opening_balance",
        )
    )).scalars().all()
    assert [row.id for row in rows] == [placeholder_id]
    assert rows[0].external_id == "gym-jan"
    assert rows[0].is_ignored is True
    assert rows[0].notes == "#health"


@pytest.mark.asyncio
async def test_a_renamed_placeholder_is_still_found_by_the_bills_wording(
    session, test_user, test_workspace, synced_account
):
    """A `set_description` rule rewrites the placeholder; the bank keeps the bill's text.

    The rule here names the placeholder's exact wording, so it renames the
    placeholder and leaves the charge alone. The charge still reads like the
    bill, and it has to land on the placeholder instead of beside it, so the
    matcher scores the placeholder under the bill's wording as well as its own.
    """
    conn, account = synced_account
    conn_id, account_id = conn.id, account.id
    await _make_bill(session, test_workspace, test_user, account)
    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Rename the gym bill",
            conditions=[RuleCondition(field="description", op="equals", value="Gym membership")],
            actions=[RuleAction(op="set_description", value="Academia")],
            apply_to_existing=False,
        ),
    )

    await generate_pending(session, test_user.id, up_to=date(2025, 1, 10))

    [placeholder] = await _generated(session, account_id)
    placeholder_id = placeholder.id
    assert placeholder.description == "Academia"
    assert placeholder.status == "pending"

    provider = _bank_reporting_the_charge(description="Gym membership Ltda")
    with patch("app.services.connection_service.get_provider", return_value=provider), \
            patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
            patch("app.services.connection_service.stamp_primary_amount", new=AsyncMock()):
        await sync_connection(session, conn_id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source != "opening_balance",
        )
    )).scalars().all()
    assert [row.id for row in rows] == [placeholder_id]
    assert rows[0].external_id == "gym-jan"
    assert rows[0].description == "Academia"


@pytest.mark.asyncio
async def test_a_later_template_edit_does_not_rename_an_older_occurrence(
    session, test_user, test_workspace, synced_account
):
    """An occurrence keeps the wording it was written with.

    Renaming the bill afterwards is a change for the occurrences still to
    come; a charge of the new wording must not absorb the placeholder that
    was written for the old one.
    """
    conn, account = synced_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account)

    await generate_pending(session, test_user.id, up_to=date(2025, 1, 10))

    [placeholder] = await _generated(session, account_id)
    placeholder_id = placeholder.id
    bill.description = "Electric utility"
    await session.commit()

    provider = _bank_reporting_the_charge(description="Electric utility")
    with patch("app.services.connection_service.get_provider", return_value=provider), \
            patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
            patch("app.services.connection_service.stamp_primary_amount", new=AsyncMock()):
        await sync_connection(session, conn_id, test_workspace.id, test_user.id)

    rows = (await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source != "opening_balance",
        )
    )).scalars().all()
    assert len(rows) == 2
    assert placeholder_id in [row.id for row in rows]
    assert "Electric utility" in [row.description for row in rows if row.id != placeholder_id]
