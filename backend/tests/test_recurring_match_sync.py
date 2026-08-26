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
from app.models.payee import Payee
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.schemas.rule import RuleAction, RuleCondition, RuleCreate
from app.services.connection_service import _description_similarity, sync_connection
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    generate_pending,
)
from app.services.rule_service import create_rule


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


async def _run_sync(
    session,
    conn_id,
    test_workspace,
    test_user,
    provider,
    *,
    mock_rules=True,
):
    patches = [
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch(
            "app.services.connection_service.detect_transfer_pairs",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.connection_service.stamp_primary_amount",
            new_callable=AsyncMock,
        ),
    ]
    if mock_rules:
        patches.append(
            patch(
                "app.services.connection_service.apply_rules_to_transaction",
                new_callable=AsyncMock,
            )
        )
    with patches[0], patches[1], patches[2]:
        if mock_rules:
            with patches[3]:
                return await sync_connection(
                    session, conn_id, test_workspace.id, test_user.id
                )
        return await sync_connection(
            session, conn_id, test_workspace.id, test_user.id
        )


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


@pytest.mark.asyncio
async def test_sync_normalizes_before_active_recurring_match(
    session,
    test_user,
    test_workspace,
    conn_account,
    test_categories,
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        account,
        description="Amazon Prime",
        amount=Decimal("19.90"),
        start_date=date(2025, 1, 10),
        category_id=test_categories[1].id,
    )
    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Normalize synced Amazon",
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
    raw_description = "D01-1234567 AMZNPrime DE changing-token"
    assert _description_similarity(raw_description, "Amazon Prime") < 0.6
    provider = _provider(
        [
            _tx(
                external_id="amazon-sync-1",
                description=raw_description,
                amount=Decimal("19.90"),
                date=date(2025, 1, 11),
                pluggy_category="Eating out",
            )
        ]
    )

    await _run_sync(
        session,
        conn_id,
        test_workspace,
        test_user,
        provider,
        mock_rules=False,
    )
    await _run_sync(
        session,
        conn_id,
        test_workspace,
        test_user,
        provider,
        mock_rules=False,
    )

    txs = await _all_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].recurring_transaction_id == bill.id
    assert txs[0].description == "Amazon Prime"
    assert txs[0].original_description == raw_description
    assert txs[0].description_is_rule_managed is True
    assert txs[0].category_id == test_categories[0].id
    assert txs[0].notes == "#subscription"
    refreshed = await session.get(RecurringTransaction, bill.id)
    assert refreshed.next_occurrence == date(2025, 2, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("placeholder_has_category", [False, True])
async def test_sync_placeholder_provider_category_precedence(
    session,
    test_user,
    test_workspace,
    conn_account,
    test_categories,
    placeholder_has_category,
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    rule_payee = Payee(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name=f"iFood Rule Target {placeholder_has_category}",
    )
    session.add(rule_payee)
    await session.flush()
    rule_payee_id = rule_payee.id

    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        account,
        description="iFood",
        amount=Decimal("49.90"),
        start_date=date(2025, 1, 10),
        category_id=test_categories[1].id,
    )
    bill.next_occurrence = date(2025, 2, 10)
    placeholder = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account_id,
        description="iFood",
        amount=Decimal("49.90"),
        currency="BRL",
        date=date(2025, 1, 10),
        type="debit",
        source="recurring",
        status="pending",
        recurring_transaction_id=bill.id,
        category_id=(
            test_categories[1].id if placeholder_has_category else None
        ),
        notes="#planned",
    )
    session.add(placeholder)
    await session.commit()
    placeholder_id = placeholder.id

    raw_description = "|fd*f|ood Club"
    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name=f"Normalize sync placeholder {placeholder_has_category}",
            conditions=[
                RuleCondition(
                    field="description", op="contains", value="f|ood Club"
                )
            ],
            actions=[
                RuleAction(op="set_description", value="iFood"),
                RuleAction(op="set_payee", value=str(rule_payee_id)),
                RuleAction(op="append_notes", value="#delivery"),
                RuleAction(op="ignore", value=True),
            ],
            apply_to_existing=False,
        ),
    )
    provider = _provider(
        [
            _tx(
                external_id=f"ifood-sync-{placeholder_has_category}",
                description=raw_description,
                payee="IFOOD.COM AGÊNCIA DE RESTAURANTES ONLINE S.A.",
                amount=Decimal("49.90"),
                date=date(2025, 1, 11),
                pluggy_category="Eating out",
                raw_data={"merchant": {"name": "IFOOD.COM"}},
            )
        ]
    )

    await _run_sync(
        session,
        conn_id,
        test_workspace,
        test_user,
        provider,
        mock_rules=False,
    )

    txs = await _all_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].id == placeholder_id
    assert txs[0].recurring_transaction_id == bill.id
    assert txs[0].category_id == (
        test_categories[1].id
        if placeholder_has_category
        else test_categories[0].id
    )
    # The placeholder keeps the recurring definition's own wording, so the
    # description was never rule-managed even though the rule matched and
    # applied its other actions.
    assert txs[0].description == "iFood"
    assert txs[0].original_description == raw_description
    assert txs[0].description_is_rule_managed is False
    assert txs[0].source == "sync"
    assert txs[0].status == "posted"
    assert txs[0].external_id == f"ifood-sync-{placeholder_has_category}"
    # Keep the provider's raw counterparty as provenance, while preserving the
    # canonical Payee chosen by the rule.
    assert txs[0].payee == "IFOOD.COM AGÊNCIA DE RESTAURANTES ONLINE S.A."
    assert txs[0].payee_id == rule_payee_id
    assert txs[0].is_ignored is True
    assert txs[0].raw_data == {"merchant": {"name": "IFOOD.COM"}}
    assert txs[0].notes == "#planned #delivery"


@pytest.mark.asyncio
async def test_sync_placeholder_rule_payee_without_raw_payee(
    session, test_user, test_workspace, conn_account
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    rule_payee = Payee(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="PR586 Sync Rule Target",
    )
    session.add(rule_payee)
    await session.flush()
    rule_payee_id = rule_payee.id

    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        account,
        description="PR586 NoPayee",
        amount=Decimal("29.90"),
        start_date=date(2025, 1, 10),
    )
    bill.next_occurrence = date(2025, 2, 10)
    placeholder = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account_id,
        description="PR586 NoPayee",
        amount=Decimal("29.90"),
        currency="BRL",
        date=date(2025, 1, 10),
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
            name="Set sync placeholder payee without raw provider payee",
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
    provider = _provider(
        [
            _tx(
                external_id="pr586-no-payee-sync",
                description="PR586 NoPayee bank",
                amount=Decimal("29.90"),
                date=date(2025, 1, 11),
            )
        ]
    )

    await _run_sync(
        session,
        conn_id,
        test_workspace,
        test_user,
        provider,
        mock_rules=False,
    )

    txs = await _all_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].id == placeholder_id
    assert txs[0].recurring_transaction_id == bill.id
    assert txs[0].payee is None
    assert txs[0].payee_id == rule_payee_id


@pytest.mark.asyncio
async def test_sync_placeholder_keeps_the_payee_the_user_already_chose(
    session, test_user, test_workspace, conn_account, test_categories
):
    """A merge fills the placeholder's gaps, it does not restate it.

    The recurring definition's payee is the user's own choice; the bank's raw
    counterparty string must not replace it. Only what is still empty is
    filled, and the raw text is kept as provenance.
    """
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    chosen_payee = Payee(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="iFood Brasil",
    )
    session.add(chosen_payee)
    await session.flush()
    chosen_payee_id = chosen_payee.id

    bill = await _make_bill(
        session,
        test_workspace,
        test_user,
        account,
        description="iFood",
        amount=Decimal("49.90"),
        start_date=date(2025, 1, 10),
    )
    bill.next_occurrence = date(2025, 2, 10)
    placeholder = Transaction(
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        account_id=account_id,
        description="iFood",
        amount=Decimal("49.90"),
        currency="BRL",
        date=date(2025, 1, 10),
        type="debit",
        source="recurring",
        status="pending",
        recurring_transaction_id=bill.id,
        payee="iFood Brasil",
        payee_id=chosen_payee_id,
        notes="#planned",
    )
    session.add(placeholder)
    await session.commit()
    placeholder_id = placeholder.id

    await create_rule(
        session,
        test_workspace.id,
        test_user.id,
        RuleCreate(
            name="Normalize iFood from the raw counterparty",
            conditions=[
                RuleCondition(field="payee", op="contains", value="IFOOD.COM")
            ],
            actions=[
                RuleAction(op="set_description", value="iFood"),
                RuleAction(op="append_notes", value="#delivery"),
            ],
            apply_to_existing=False,
        ),
    )
    raw_description = "|fd*f|ood Club"
    provider = _provider(
        [
            _tx(
                external_id="ifood-keeps-payee",
                description=raw_description,
                payee="IFOOD.COM AGÊNCIA DE RESTAURANTES ONLINE S.A.",
                amount=Decimal("49.90"),
                date=date(2025, 1, 11),
            )
        ]
    )

    await _run_sync(
        session, conn_id, test_workspace, test_user, provider, mock_rules=False
    )

    txs = await _all_txs(session, account_id)
    assert len(txs) == 1
    assert txs[0].id == placeholder_id
    assert txs[0].payee == "iFood Brasil"
    assert txs[0].payee_id == chosen_payee_id
    assert txs[0].description == "iFood"
    assert txs[0].original_description == raw_description
    # The gaps still get filled and the rule's own action still lands.
    assert txs[0].notes == "#planned #delivery"
    assert txs[0].external_id == "ifood-keeps-payee"


# ---------------------------------------------------------------------------
# Placeholder-first: incoming charge merges into the generated row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_promotes_pending_placeholder_to_posted(
    session, test_user, test_workspace, conn_account
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account,
                            start_date=date(2025, 1, 10))
    bill_id = bill.id
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 1
    placeholder = (await session.execute(
        select(Transaction).where(Transaction.source == "recurring")
    )).scalar_one()
    placeholder_id = placeholder.id
    # Synced account: the placeholder is held pending until a real
    # charge confirms it, so a missed match never inflates the balance.
    assert placeholder.status == "pending"

    provider = _provider([_tx(external_id="s1", description="NETFLIX SUBSCRIPTION",
                              amount=Decimal("39.90"), date=date(2025, 1, 11))])
    await _run_sync(session, conn_id, test_workspace, test_user, provider)

    txs = await _all_txs(session, account_id)
    assert len(txs) == 1  # merged, not duplicated
    merged = txs[0]
    assert merged.id == placeholder_id
    assert merged.external_id == "s1"
    assert merged.source == "sync"
    assert merged.status == "posted"
    assert merged.recurring_transaction_id == bill_id
    assert merged.original_description == "NETFLIX SUBSCRIPTION"
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
