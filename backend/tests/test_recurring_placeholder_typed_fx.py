"""Recurring placeholder takeover drops stale typed primary amounts (#1796).

A foreign-currency recurring placeholder can carry a user-typed primary
conversion (`amount_primary_typed` / `amount_primary_typed_currency`, ADR 0031).
When a real charge from CSV/OFX import or bank sync takes over that placeholder
and replaces its amount, currency, or conversion, the typed columns must be
cleared so a subsequent display-currency round trip does not resurrect the
placeholder's stale estimate over the real charge.
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
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User
from app.providers.base import AccountData, TransactionData
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.schemas.transaction import TransactionImport
from app.services import reconciliation_policy, reconciliation_rule_service
from app.services.connection_service import sync_connection
from app.services.fx_rate_service import ACCOUNT_AMOUNT_FROM_USER
from app.services.import_service import import_transactions
from app.services.primary_restamp_service import restamp_user_primary_amounts
from app.services.recurring_transaction_service import create_recurring_transaction

D_BILL = date(2026, 1, 10)
D_CHARGE = date(2026, 1, 11)


@pytest_asyncio.fixture
async def usd_brl_rates(session: AsyncSession, clean_db) -> None:
    """Stored USD -> BRL rate distinct from both the placeholder estimate
    (90.00 / 18.43 ~= 4.8833) and the imported/bank rates (5.0 / 5.28)."""
    for on in (D_BILL, D_CHARGE):
        session.add(
            FxRate(
                base_currency="USD",
                quote_currency="BRL",
                date=on,
                rate=Decimal("5.50"),
                source="test",
            )
        )
    await session.commit()


@pytest_asyncio.fixture
async def sync_conn_account(session: AsyncSession, test_user: User, test_workspace):
    conn = BankConnection(
        id=uuid.uuid4(),
        user_id=test_user.id,
        provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Sync Bank",
        credentials={"token": "fake"},
        status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        connection_id=conn.id,
        workspace_id=test_workspace.id,
        name="BRL Card",
        type="checking",
        external_id="acc-ext-1",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(account)
    await session.commit()
    await session.refresh(conn)
    await session.refresh(account)
    return conn, account


async def _enable_amount_tolerance(session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await reconciliation_rule_service.upsert_override(
        session,
        workspace_id,
        user_id,
        reconciliation_policy.MATCH_RECURRING["node"],
        "same_account_exact",
        {
            "when": {
                "amount": {"match": "tolerance", "percent": "5"},
                "unique_candidate": True,
            }
        },
    )


async def _make_typed_usd_placeholder(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    account: Account,
) -> uuid.UUID:
    """A 18.43 USD recurring placeholder on a BRL account with a user-typed
    90.00 BRL conversion."""
    bill = await create_recurring_transaction(
        session,
        workspace_id,
        user_id,
        RecurringTransactionCreate(
            description="Netflix Subscription",
            amount=Decimal("18.43"),
            currency="USD",
            type="debit",
            frequency="monthly",
            start_date=D_BILL,
            account_id=account.id,
        ),
    )
    bill.next_occurrence = date(2026, 2, 10)
    placeholder = Transaction(
        id=uuid.uuid4(),
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=account.id,
        description="Netflix Subscription",
        amount=Decimal("18.43"),
        currency="USD",
        date=D_BILL,
        amount_primary=Decimal("90.00"),
        fx_rate_used=Decimal("4.8833"),
        amount_primary_typed=Decimal("90.00"),
        amount_primary_typed_currency="BRL",
        amount_account=Decimal("90.00"),
        amount_account_source=ACCOUNT_AMOUNT_FROM_USER,
        type="debit",
        source="recurring",
        status="posted",
        recurring_transaction_id=bill.id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(placeholder)
    await session.commit()
    return placeholder.id


async def _real_txs(session: AsyncSession, account_id: uuid.UUID) -> list[Transaction]:
    result = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source != "opening_balance",
        )
    )
    return list(result.scalars().all())


async def _switch_display_currency(
    session: AsyncSession, user_id: uuid.UUID, currency: str
) -> None:
    user = await session.get(User, user_id)
    assert user is not None
    user.preferences = {**(user.preferences or {}), "currency_display": currency}
    await session.flush()
    await restamp_user_primary_amounts(session, user_id)
    await session.commit()


def _mock_provider(transactions: list[TransactionData], account_ext: str = "acc-ext-1") -> AsyncMock:
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_accounts = AsyncMock(
        return_value=[
            AccountData(
                external_id=account_ext,
                name="BRL Card",
                type="checking",
                balance=Decimal("0"),
                currency="BRL",
            )
        ]
    )
    provider.get_transactions = AsyncMock(return_value=transactions)
    return provider


async def _run_real_sync(
    session: AsyncSession,
    conn_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    provider: AsyncMock,
) -> None:
    with (
        patch("app.services.connection_service.get_provider", return_value=provider),
        patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock),
        patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock),
    ):
        await sync_connection(session, conn_id, workspace_id, user_id)


# ---------------------------------------------------------------------------
# Import path (`import_service.import_transactions`)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_repro_1796_clears_typed_primary_and_survives_round_trip(
    session: AsyncSession, test_user: User, test_workspace, test_account: Account, usd_brl_rates
):
    """Exact #1796 repro: 18.43 USD placeholder typed as 90.00 BRL taken over by
    a CSV row of 18.60 USD at fx_rate=5.0 (= 93.00 BRL). Typed columns are
    cleared so BRL -> USD -> BRL restamp keeps 93.00, not stale 90.00."""
    account_id, user_id, workspace_id = test_account.id, test_user.id, test_workspace.id
    await _enable_amount_tolerance(session, workspace_id, user_id)
    placeholder_id = await _make_typed_usd_placeholder(
        session, workspace_id, user_id, test_account
    )

    incoming = [
        TransactionImport(
            description="NETFLIX SUBSCRIPTION",
            amount=Decimal("18.60"),
            date=D_CHARGE,
            type="debit",
            currency="USD",
            fx_rate=Decimal("5.0"),
        )
    ]
    with patch("app.services.import_service.apply_rules_to_transaction", new_callable=AsyncMock):
        imported, skipped, _, _ = await import_transactions(
            session, workspace_id, user_id, account_id, incoming, "csv"
        )

    assert (imported, skipped) == (1, 0)
    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.id == placeholder_id
    assert tx.amount == Decimal("18.60")
    assert tx.amount_primary == Decimal("93.00")
    assert tx.fx_rate_used == Decimal("5.0")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (None, None)

    await _switch_display_currency(session, user_id, "USD")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("18.60")

    await _switch_display_currency(session, user_id, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("93.00")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (None, None)


@pytest.mark.asyncio
async def test_import_placeholder_fx_rate_override_clears_typed_primary(
    session: AsyncSession, test_user: User, test_workspace, test_account: Account, usd_brl_rates
):
    """Matching amount/currency with an explicit fx_rate in the file replaces
    the placeholder's primary amount and drops its stale typed conversion."""
    account_id, user_id, workspace_id = test_account.id, test_user.id, test_workspace.id
    placeholder_id = await _make_typed_usd_placeholder(
        session, workspace_id, user_id, test_account
    )

    incoming = [
        TransactionImport(
            description="NETFLIX SUBSCRIPTION",
            amount=Decimal("18.43"),
            date=D_CHARGE,
            type="debit",
            currency="USD",
            fx_rate=Decimal("5.28"),
        )
    ]
    with patch("app.services.import_service.apply_rules_to_transaction", new_callable=AsyncMock):
        imported, _, _, _ = await import_transactions(
            session, workspace_id, user_id, account_id, incoming, "csv"
        )

    assert imported == 1
    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.id == placeholder_id
    assert tx.amount_primary == Decimal("97.31")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (None, None)


@pytest.mark.asyncio
async def test_import_placeholder_currency_change_clears_typed_primary(
    session: AsyncSession, test_user: User, test_workspace, test_account: Account, usd_brl_rates
):
    """A BRL statement charge answering a USD placeholder takes over in BRL
    and clears the stale USD->BRL typed conversion."""
    account_id, user_id, workspace_id = test_account.id, test_user.id, test_workspace.id
    placeholder_id = await _make_typed_usd_placeholder(
        session, workspace_id, user_id, test_account
    )

    incoming = [
        TransactionImport(
            description="NETFLIX SUBSCRIPTION",
            amount=Decimal("97.30"),
            date=D_CHARGE,
            type="debit",
            currency="BRL",
        )
    ]
    with patch("app.services.import_service.apply_rules_to_transaction", new_callable=AsyncMock):
        imported, _, _, _ = await import_transactions(
            session, workspace_id, user_id, account_id, incoming, "csv"
        )

    assert imported == 1
    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.id == placeholder_id
    assert (tx.amount, tx.currency) == (Decimal("97.30"), "BRL")
    assert tx.amount_primary == Decimal("97.30")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (None, None)

    await _switch_display_currency(session, user_id, "USD")
    await _switch_display_currency(session, user_id, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("97.30")


@pytest.mark.asyncio
async def test_import_placeholder_same_amount_currency_without_fx_rate_keeps_typed_primary(
    session: AsyncSession, test_user: User, test_workspace, test_account: Account, usd_brl_rates
):
    """When the imported charge matches the placeholder's exact amount and
    currency with no fx_rate override, the user's typed conversion stays intact."""
    account_id, user_id, workspace_id = test_account.id, test_user.id, test_workspace.id
    placeholder_id = await _make_typed_usd_placeholder(
        session, workspace_id, user_id, test_account
    )

    incoming = [
        TransactionImport(
            description="NETFLIX SUBSCRIPTION",
            amount=Decimal("18.43"),
            date=D_CHARGE,
            type="debit",
            currency="USD",
        )
    ]
    with patch("app.services.import_service.apply_rules_to_transaction", new_callable=AsyncMock):
        imported, _, _, _ = await import_transactions(
            session, workspace_id, user_id, account_id, incoming, "csv"
        )

    assert imported == 1
    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.id == placeholder_id
    assert tx.amount_primary == Decimal("90.00")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (
        Decimal("90.00"),
        "BRL",
    )

    await _switch_display_currency(session, user_id, "USD")
    await _switch_display_currency(session, user_id, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("90.00")


# ---------------------------------------------------------------------------
# Bank sync path (`connection_service.sync_connection`)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_placeholder_amount_change_clears_typed_primary(
    session: AsyncSession, test_user: User, test_workspace, sync_conn_account, usd_brl_rates
):
    """Synced charge with a tolerated amount difference replaces the placeholder's
    amount and clears its typed conversion."""
    conn, account = sync_conn_account
    conn_id, account_id = conn.id, account.id
    user_id, workspace_id = test_user.id, test_workspace.id
    await _enable_amount_tolerance(session, workspace_id, user_id)
    placeholder_id = await _make_typed_usd_placeholder(
        session, workspace_id, user_id, account
    )

    provider = _mock_provider(
        [
            TransactionData(
                external_id="sync-amt-1",
                description="NETFLIX SUBSCRIPTION",
                amount=Decimal("18.60"),
                date=D_CHARGE,
                type="debit",
                currency="USD",
                status="posted",
            )
        ]
    )
    await _run_real_sync(session, conn_id, workspace_id, user_id, provider)

    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.id == placeholder_id
    assert tx.amount == Decimal("18.60")
    # 18.60 * 5.50 = 102.30 from stored FX rate.
    assert tx.amount_primary == Decimal("102.30")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (None, None)

    await _switch_display_currency(session, user_id, "USD")
    await _switch_display_currency(session, user_id, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("102.30")


@pytest.mark.asyncio
async def test_sync_placeholder_currency_change_clears_typed_primary(
    session: AsyncSession, test_user: User, test_workspace, sync_conn_account, usd_brl_rates
):
    """A synced local BRL charge answering a USD placeholder clears the stale
    typed conversion."""
    conn, account = sync_conn_account
    conn_id, account_id = conn.id, account.id
    user_id, workspace_id = test_user.id, test_workspace.id
    placeholder_id = await _make_typed_usd_placeholder(
        session, workspace_id, user_id, account
    )

    provider = _mock_provider(
        [
            TransactionData(
                external_id="sync-cur-1",
                description="NETFLIX SUBSCRIPTION",
                amount=Decimal("97.30"),
                date=D_CHARGE,
                type="debit",
                currency="BRL",
                status="posted",
            )
        ]
    )
    await _run_real_sync(session, conn_id, workspace_id, user_id, provider)

    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.id == placeholder_id
    assert (tx.amount, tx.currency) == (Decimal("97.30"), "BRL")
    assert tx.amount_primary == Decimal("97.30")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (None, None)

    await _switch_display_currency(session, user_id, "USD")
    await _switch_display_currency(session, user_id, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("97.30")


@pytest.mark.asyncio
async def test_sync_placeholder_bank_converted_amount_clears_typed_primary(
    session: AsyncSession, test_user: User, test_workspace, sync_conn_account, usd_brl_rates
):
    """When the bank reports its own conversion into the BRL account (= user
    primary currency), it replaces the placeholder's typed estimate and survives
    a display-currency round trip."""
    conn, account = sync_conn_account
    conn_id, account_id = conn.id, account.id
    user_id, workspace_id = test_user.id, test_workspace.id
    placeholder_id = await _make_typed_usd_placeholder(
        session, workspace_id, user_id, account
    )

    provider = _mock_provider(
        [
            TransactionData(
                external_id="sync-conv-1",
                description="NETFLIX SUBSCRIPTION",
                amount=Decimal("18.43"),
                date=D_CHARGE,
                type="debit",
                currency="USD",
                amount_in_account_currency=Decimal("97.30"),
                status="posted",
            )
        ]
    )
    await _run_real_sync(session, conn_id, workspace_id, user_id, provider)

    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.id == placeholder_id
    assert tx.amount == Decimal("18.43")
    assert tx.amount_primary == Decimal("97.30")
    assert tx.amount_account == Decimal("97.30")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (None, None)

    await _switch_display_currency(session, user_id, "USD")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("18.43")

    await _switch_display_currency(session, user_id, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("97.30")


@pytest.mark.asyncio
async def test_sync_placeholder_account_charge_outside_primary_clears_typed_primary(
    session: AsyncSession, test_user: User, test_workspace, sync_conn_account, usd_brl_rates
):
    """User typed 90.00 BRL on a 18.43 USD placeholder, switched display to USD,
    then bank sync brought the real 97.30 BRL account charge (`converted` is None
    while display is USD, only `amount_in_account_currency` is set). Clearing the
    stale BRL typed conversion ensures switching back to BRL uses the bank's 97.30."""
    conn, account = sync_conn_account
    conn_id, account_id = conn.id, account.id
    user_id, workspace_id = test_user.id, test_workspace.id
    placeholder_id = await _make_typed_usd_placeholder(
        session, workspace_id, user_id, account
    )
    await _switch_display_currency(session, user_id, "USD")

    provider = _mock_provider(
        [
            TransactionData(
                external_id="sync-acct-only-1",
                description="NETFLIX SUBSCRIPTION",
                amount=Decimal("18.43"),
                date=D_CHARGE,
                type="debit",
                currency="USD",
                amount_in_account_currency=Decimal("97.30"),
                status="posted",
            )
        ]
    )
    await _run_real_sync(session, conn_id, workspace_id, user_id, provider)

    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.id == placeholder_id
    assert tx.amount_primary == Decimal("18.43")
    assert tx.amount_account == Decimal("97.30")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (None, None)

    await _switch_display_currency(session, user_id, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("97.30")


@pytest.mark.asyncio
async def test_sync_placeholder_identical_charge_without_conversion_keeps_typed_primary(
    session: AsyncSession, test_user: User, test_workspace, sync_conn_account, usd_brl_rates
):
    """Matching amount and currency with no bank conversion leaves the user's
    typed primary conversion intact across display-currency round trips."""
    conn, account = sync_conn_account
    conn_id, account_id = conn.id, account.id
    user_id, workspace_id = test_user.id, test_workspace.id
    placeholder_id = await _make_typed_usd_placeholder(
        session, workspace_id, user_id, account
    )

    provider = _mock_provider(
        [
            TransactionData(
                external_id="sync-same-1",
                description="NETFLIX SUBSCRIPTION",
                amount=Decimal("18.43"),
                date=D_CHARGE,
                type="debit",
                currency="USD",
                status="posted",
            )
        ]
    )
    await _run_real_sync(session, conn_id, workspace_id, user_id, provider)

    txs = await _real_txs(session, account_id)
    assert len(txs) == 1
    tx = txs[0]
    assert tx.id == placeholder_id
    assert tx.amount_primary == Decimal("90.00")
    assert (tx.amount_primary_typed, tx.amount_primary_typed_currency) == (
        Decimal("90.00"),
        "BRL",
    )

    await _switch_display_currency(session, user_id, "USD")
    await _switch_display_currency(session, user_id, "BRL")
    await session.refresh(tx)
    assert tx.amount_primary == Decimal("90.00")
