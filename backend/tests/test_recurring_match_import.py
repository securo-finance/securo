"""Integration tests for recurring-bill matching through the CSV/OFX import
path (issue #116)."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.payee import Payee
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.schemas.rule import RuleAction, RuleCondition, RuleCreate
from app.schemas.transaction import TransactionImport
from app.services.import_service import import_transactions
from app.services.recurring_transaction_service import create_recurring_transaction
from app.services.rule_service import create_rule


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
    assert txs[0].original_description == "NETFLIX SUBSCRIPTION"


@pytest.mark.asyncio
async def test_import_preserves_original_description_without_normalization(
    session, test_user, test_workspace, test_account
):
    incoming = TransactionImport(
        description="BANK RAW DESCRIPTION",
        amount=Decimal("12.34"),
        date=date(2026, 1, 11),
        type="debit",
        currency="BRL",
    )

    imported, skipped, _, _ = await _run_import(
        session,
        test_workspace,
        test_user,
        test_account.id,
        [incoming],
    )

    assert (imported, skipped) == (1, 0)
    txs = await _real_txs(session, test_account.id)
    assert len(txs) == 1
    assert txs[0].description == incoming.description
    assert txs[0].original_description == incoming.description
    assert txs[0].description_is_rule_managed is False

@pytest.mark.asyncio
async def test_import_normalizes_before_recurring_match_and_deduplicates_raw_reimport(
    session, test_user, test_workspace, test_account
):
    account_id = test_account.id
    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        test_account,
        description="Amazon Prime",
        amount=Decimal("19.90"),
        start_date=date(2026, 1, 10),
    )
    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Normalize Amazon",
            conditions=[
                RuleCondition(
                    field="description", op="contains", value="AMZNPrime DE"
                )
            ],
            actions=[
                RuleAction(op="set_description", value="Amazon Prime"),
                RuleAction(op="append_notes", value="#subscription"),
            ],
            apply_to_existing=False,
        ),
    )
    noisy = TransactionImport(
        description="D01-123 AMZNPrime DE changing-token",
        amount=Decimal("19.90"),
        date=date(2026, 1, 11),
        type="debit",
        currency="BRL",
    )

    with patch(
        "app.services.import_service.stamp_primary_amount",
        new_callable=AsyncMock,
    ):
        imported, skipped, _, _ = await import_transactions(
            session,
            test_workspace.id,
            test_user.id,
            account_id,
            [noisy],
            "csv",
        )
        imported_again, skipped_again, _, _ = await import_transactions(
            session,
            test_workspace.id,
            test_user.id,
            account_id,
            [noisy],
            "csv",
        )

    assert (imported, skipped) == (1, 0)
    assert (imported_again, skipped_again) == (0, 1)
    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].recurring_transaction_id == bill.id
    assert txs[0].description == "Amazon Prime"
    assert txs[0].original_description == noisy.description
    assert txs[0].description_is_rule_managed is True
    assert txs[0].notes == "#subscription"


@pytest.mark.asyncio
async def test_import_normalizes_before_placeholder_upgrade(
    session, test_user, test_workspace, test_account, test_categories
):
    account_id = test_account.id
    rule_payee = Payee(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="iFood Rule Target",
    )
    session.add(rule_payee)
    await session.flush()
    rule_payee_id = rule_payee.id

    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        test_account,
        description="iFood",
        amount=Decimal("49.90"),
        start_date=date(2026, 1, 10),
    )
    bill.next_occurrence = date(2026, 2, 10)
    placeholder = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account_id,
        description="iFood",
        amount=Decimal("49.90"),
        currency="BRL",
        date=date(2026, 1, 10),
        type="debit",
        source="recurring",
        status="pending",
        recurring_transaction_id=bill.id,
        category_id=test_categories[0].id,
    )
    session.add(placeholder)
    await session.commit()
    placeholder_id = placeholder.id

    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Normalize raw iFood transaction",
            conditions=[
                RuleCondition(
                    field="description", op="contains", value="f|ood Club"
                )
            ],
            actions=[
                RuleAction(op="set_description", value="iFood"),
                RuleAction(op="set_payee", value=str(rule_payee_id)),
                RuleAction(op="ignore", value=True),
            ],
            apply_to_existing=False,
        ),
    )
    incoming = TransactionImport(
        description="|fd*f|ood Club",
        payee_raw="IFOOD.COM AGÊNCIA DE RESTAURANTES ONLINE S.A.",
        amount=Decimal("49.90"),
        date=date(2026, 1, 11),
        type="debit",
        currency="BRL",
        category_id=test_categories[1].id,
        notes="#statement",
    )

    with patch(
        "app.services.import_service.stamp_primary_amount",
        new_callable=AsyncMock,
    ):
        await import_transactions(
            session,
            test_workspace.id,
            test_user.id,
            account_id,
            [incoming],
            "ofx",
        )

    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].id == placeholder_id
    assert txs[0].recurring_transaction_id == bill.id
    # The placeholder keeps the recurring definition's own wording, so the
    # description was never rule-managed even though the rule matched and
    # applied its other actions.
    assert txs[0].description == "iFood"
    assert txs[0].original_description == "|fd*f|ood Club"
    assert txs[0].description_is_rule_managed is False
    assert txs[0].source == "ofx"
    assert txs[0].status == "posted"
    assert txs[0].import_id is not None
    # Keep the provider's raw counterparty as provenance, while preserving the
    # canonical Payee chosen by the rule.
    assert txs[0].payee == "IFOOD.COM AGÊNCIA DE RESTAURANTES ONLINE S.A."
    assert txs[0].payee_id == rule_payee_id
    assert txs[0].is_ignored is True
    assert txs[0].notes == "#statement"
    assert txs[0].category_id == test_categories[0].id


@pytest.mark.asyncio
async def test_import_placeholder_rule_payee_without_raw_payee(
    session, test_user, test_workspace, test_account
):
    account_id = test_account.id
    rule_payee = Payee(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="PR586 Rule Target",
    )
    session.add(rule_payee)
    await session.flush()
    rule_payee_id = rule_payee.id

    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        test_account,
        description="PR586 NoPayee",
        amount=Decimal("29.90"),
        start_date=date(2026, 1, 10),
    )
    bill.next_occurrence = date(2026, 2, 10)
    placeholder = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account_id,
        description="PR586 NoPayee",
        amount=Decimal("29.90"),
        currency="BRL",
        date=date(2026, 1, 10),
        type="debit",
        source="recurring",
        status="pending",
        recurring_transaction_id=bill.id,
    )
    session.add(placeholder)
    await session.commit()
    placeholder_id = placeholder.id

    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Set placeholder payee without raw import payee",
            conditions=[
                RuleCondition(
                    field="description",
                    op="contains",
                    value="PR586 NoPayee bank",
                )
            ],
            actions=[
                RuleAction(op="set_description", value="PR586 NoPayee"),
                RuleAction(op="set_payee", value=str(rule_payee_id)),
            ],
            apply_to_existing=False,
        ),
    )
    incoming = TransactionImport(
        description="PR586 NoPayee bank",
        amount=Decimal("29.90"),
        date=date(2026, 1, 11),
        type="debit",
        currency="BRL",
    )

    with patch(
        "app.services.import_service.stamp_primary_amount",
        new_callable=AsyncMock,
    ):
        await import_transactions(
            session,
            test_workspace.id,
            test_user.id,
            account_id,
            [incoming],
            "csv",
        )

    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].id == placeholder_id
    assert txs[0].recurring_transaction_id == bill.id
    assert txs[0].payee is None
    assert txs[0].payee_id == rule_payee_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("incoming_category", "force_uncategorized", "expected_category"),
    [
        (True, False, "incoming"),
        (False, False, "rule"),
        (False, True, None),
    ],
)
async def test_import_placeholder_category_precedence(
    session,
    test_user,
    test_workspace,
    test_account,
    test_categories,
    incoming_category,
    force_uncategorized,
    expected_category,
):
    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        test_account,
        description="iFood",
        amount=Decimal("59.90"),
        start_date=date(2026, 2, 10),
    )
    bill.next_occurrence = date(2026, 3, 10)
    placeholder = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=test_account.id,
        description="iFood",
        amount=Decimal("59.90"),
        currency="BRL",
        date=date(2026, 2, 10),
        type="debit",
        source="recurring",
        status="posted",
        recurring_transaction_id=bill.id,
    )
    session.add(placeholder)
    await session.commit()
    placeholder_id = placeholder.id
    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name=(
                "Import placeholder precedence "
                f"{incoming_category}-{force_uncategorized}"
            ),
            conditions=[
                RuleCondition(
                    field="description", op="contains", value="noisy iFood"
                )
            ],
            actions=[
                RuleAction(op="set_description", value="iFood"),
                RuleAction(
                    op="set_category", value=str(test_categories[0].id)
                ),
                RuleAction(op="append_notes", value="#delivery"),
            ],
            apply_to_existing=False,
        ),
    )
    incoming = TransactionImport(
        description="changing noisy iFood token",
        amount=Decimal("59.90"),
        date=date(2026, 2, 11),
        type="debit",
        currency="BRL",
        category_id=(
            test_categories[1].id if incoming_category else None
        ),
        force_uncategorized=force_uncategorized,
    )

    with patch(
        "app.services.import_service.stamp_primary_amount",
        new_callable=AsyncMock,
    ):
        await import_transactions(
            session,
            test_workspace.id,
            test_user.id,
            test_account.id,
            [incoming],
            "csv",
        )

    txs = await _real_txs(session, test_account.id)
    assert len(txs) == 1
    assert txs[0].id == placeholder_id
    assert txs[0].recurring_transaction_id == bill.id
    if expected_category == "incoming":
        assert txs[0].category_id == test_categories[1].id
    elif expected_category == "rule":
        assert txs[0].category_id == test_categories[0].id
    else:
        assert txs[0].category_id is None
    assert txs[0].description == "iFood"
    assert txs[0].original_description == incoming.description
    assert txs[0].notes == "#delivery"


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
