"""Pending rows that post after they leave the incremental window (#844).

Pluggy lists incremental syncs by `createdAt`, and a row keeps its
`createdAt` when it flips PENDING → POSTED. A card purchase is pending until
its bill closes, weeks later, so the regular listing stops returning it
before it posts. The sync re-reads those rows by transaction date. That
listing also returns rows the user deleted or merged long ago, so it may only
upgrade pending rows, and it must stay out of the phantom cleanup's evidence.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.credit_card_bill import CreditCardBill
from app.models.transaction import Transaction
from app.providers.base import AccountData, BillData, TransactionData
from app.services.connection_service import sync_connection

ACCOUNT_EXT_ID = "card-1"


async def _setup(session: AsyncSession, user_id, *, last_sync=True):
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user_id, provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Nubank", credentials={"token": "fake"},
        status="active",
        last_sync_at=datetime.now(timezone.utc) if last_sync else None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    account = Account(
        id=uuid.uuid4(), user_id=user_id, connection_id=conn.id,
        external_id=ACCOUNT_EXT_ID, name="Card", type="credit_card",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    return conn, account


def _add_tx(session, user_id, account, external_id, days_ago, **overrides):
    fields = dict(
        id=uuid.uuid4(), user_id=user_id, account_id=account.id,
        external_id=external_id, description=f"PURCHASE {external_id}",
        amount=Decimal("50"), date=app_today() - timedelta(days=days_ago),
        type="debit", currency="BRL", source="sync", status="pending",
        created_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    tx = Transaction(**fields)
    session.add(tx)
    return tx


def _posted(
    external_id: str,
    days_ago: int,
    *,
    description: str | None = None,
    amount: Decimal = Decimal("50"),
    status: str = "posted",
    bill_external_id: str | None = None,
) -> TransactionData:
    return TransactionData(
        external_id=external_id,
        description=description or f"PURCHASE {external_id}",
        amount=amount, date=app_today() - timedelta(days=days_ago),
        type="debit", currency="BRL", status=status,
        bill_external_id=bill_external_id,
    )


def _provider(listed=(), reread=(), bills=()):
    provider = AsyncMock()
    provider.refresh_credentials = AsyncMock(return_value={"token": "t"})
    provider.get_accounts = AsyncMock(return_value=[
        AccountData(external_id=ACCOUNT_EXT_ID, name="Card", type="credit_card",
                    balance=Decimal("0"), currency="BRL"),
    ])
    provider.get_bills = AsyncMock(return_value=list(bills))
    provider.get_holdings = AsyncMock(return_value=[])
    provider.get_transactions = AsyncMock(return_value=list(listed))
    provider.get_transactions_by_date = AsyncMock(return_value=list(reread))
    return provider


async def _sync(session, conn, workspace_id, user_id, provider):
    with patch("app.services.connection_service.get_provider", return_value=provider), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock), \
         patch("app.services.connection_service.stamp_primary_amount", new_callable=AsyncMock), \
         patch("app.services.connection_service.apply_rules_to_transaction", new_callable=AsyncMock):
        await sync_connection(session, conn.id, workspace_id, user_id)


async def _by_external_id(session, external_id):
    return (await session.execute(
        select(Transaction).where(Transaction.external_id == external_id)
    )).scalar_one_or_none()


@pytest.mark.asyncio
async def test_pending_row_outside_the_window_posts_and_joins_its_bill(
    session: AsyncSession, test_user, test_workspace,
):
    conn, account = await _setup(session, test_user.id)
    _add_tx(session, test_user.id, account, "old-pending", 30)
    await session.commit()

    provider = _provider(
        reread=[_posted("old-pending", 30, bill_external_id="bill-1")],
        bills=[BillData(external_id="bill-1", due_date=app_today(),
                        total_amount=Decimal("50"))],
    )
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    provider.get_transactions_by_date.assert_awaited_once()
    assert provider.get_transactions_by_date.await_args.args[2] == (
        app_today() - timedelta(days=30)
    )
    row = await _by_external_id(session, "old-pending")
    bill = (await session.execute(
        select(CreditCardBill).where(CreditCardBill.external_id == "bill-1")
    )).scalar_one()
    assert row.status == "posted"
    assert row.bill_id == bill.id


@pytest.mark.asyncio
async def test_reread_starts_at_the_oldest_pending_row_not_returned(
    session: AsyncSession, test_user, test_workspace,
):
    conn, account = await _setup(session, test_user.id)
    _add_tx(session, test_user.id, account, "pending-20", 20)
    _add_tx(session, test_user.id, account, "pending-40", 40)
    # Older still, but the regular listing returned it: no reason to re-read.
    _add_tx(session, test_user.id, account, "pending-60-listed", 60)
    await session.commit()

    provider = _provider(listed=[
        _posted("pending-60-listed", 60, status="pending"),
    ])
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    assert provider.get_transactions_by_date.await_args.args[2] == (
        app_today() - timedelta(days=40)
    )


@pytest.mark.asyncio
async def test_no_reread_when_no_pending_row_qualifies(
    session: AsyncSession, test_user, test_workspace,
):
    """Future installments are pending by design, ignored rows are frozen,
    and a row still pending after the window is one the provider keeps
    pending; none of them may widen every sync."""
    conn, account = await _setup(session, test_user.id)
    _add_tx(session, test_user.id, account, "future-installment", -20)
    _add_tx(session, test_user.id, account, "ignored", 30, is_ignored=True)
    _add_tx(session, test_user.id, account, "ancient", 120)
    _add_tx(session, test_user.id, account, "posted", 30, status="posted")
    await session.commit()

    provider = _provider()
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    provider.get_transactions_by_date.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_sync_does_not_reread(
    session: AsyncSession, test_user, test_workspace,
):
    """The first sync lists the full history, so nothing is out of reach."""
    conn, account = await _setup(session, test_user.id, last_sync=False)
    _add_tx(session, test_user.id, account, "old-pending", 30)
    await session.commit()

    provider = _provider()
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    provider.get_transactions_by_date.assert_not_awaited()


@pytest.mark.asyncio
async def test_reread_does_not_bring_back_deleted_rows(
    session: AsyncSession, test_user, test_workspace,
):
    conn, account = await _setup(session, test_user.id)
    _add_tx(session, test_user.id, account, "old-pending", 30)
    account_id = account.id
    await session.commit()

    provider = _provider(reread=[
        _posted("old-pending", 30),
        _posted("deleted-by-user", 25, amount=Decimal("80")),
    ])
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    assert await _by_external_id(session, "deleted-by-user") is None
    count = await session.scalar(
        select(func.count()).select_from(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source == "sync",
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_reread_leaves_manual_rows_alone(
    session: AsyncSession, test_user, test_workspace,
):
    """The regular listing folds a matching manual row into a synced one.
    The re-read must not: that decision was made, or skipped, back when the
    row was new."""
    conn, account = await _setup(session, test_user.id)
    _add_tx(session, test_user.id, account, "old-pending", 30)
    manual = _add_tx(
        session, test_user.id, account, None, 25,
        source="manual", status="posted", amount=Decimal("80"),
        description="PURCHASE manual-twin",
    )
    await session.commit()

    provider = _provider(reread=[
        _posted("old-pending", 30),
        _posted("manual-twin", 25, amount=Decimal("80")),
    ])
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    await session.refresh(manual)
    assert manual.source == "manual"
    assert manual.external_id is None


@pytest.mark.asyncio
async def test_pending_row_rekeyed_on_posting_is_upgraded_in_place(
    session: AsyncSession, test_user, test_workspace,
):
    conn, account = await _setup(session, test_user.id)
    pending = _add_tx(session, test_user.id, account, "pending-id", 30)
    account_id = account.id
    await session.commit()

    provider = _provider(reread=[_posted("posted-id", 30, description="PURCHASE pending-id")])
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    await session.refresh(pending)
    assert pending.status == "posted"
    assert pending.external_id == "posted-id"
    count = await session.scalar(
        select(func.count()).select_from(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source == "sync",
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_reread_leaves_ignored_rows_pending(
    session: AsyncSession, test_user, test_workspace,
):
    conn, account = await _setup(session, test_user.id)
    _add_tx(session, test_user.id, account, "old-pending", 30)
    ignored = _add_tx(session, test_user.id, account, "ignored", 20, is_ignored=True)
    await session.commit()

    provider = _provider(reread=[_posted("old-pending", 30), _posted("ignored", 20)])
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    await session.refresh(ignored)
    assert ignored.status == "pending"


@pytest.mark.asyncio
async def test_reread_ids_stay_out_of_phantom_cleanup(
    session: AsyncSession, test_user, test_workspace,
):
    """A re-read starting on an arbitrary date can return one twin of a pair
    that straddles that date, which the cleanup would read as a re-key
    (#1038). It only gets what the regular listing returned."""
    conn, account = await _setup(session, test_user.id)
    _add_tx(session, test_user.id, account, "old-pending", 30)
    account_id = account.id
    await session.commit()

    provider = _provider(
        listed=[_posted("recent", 1)],
        reread=[_posted("old-pending", 30), _posted("straddler", 29)],
    )
    with patch(
        "app.services.connection_service._cleanup_phantom_duplicates",
        new_callable=AsyncMock, return_value=set(),
    ) as cleanup:
        await _sync(session, conn, test_workspace.id, test_user.id, provider)

    cleanup.assert_awaited_once()
    live_ids = cleanup.await_args_list[0].args[2]
    assert live_ids == {account_id: {"recent"}}


@pytest.mark.asyncio
async def test_failed_reread_leaves_the_sync_intact(
    session: AsyncSession, test_user, test_workspace,
):
    """The re-read is supplementary: if it fails, the regular listing still
    lands and the connection stays healthy, instead of every sync failing
    while the pending row is around."""
    conn, account = await _setup(session, test_user.id)
    _add_tx(session, test_user.id, account, "old-pending", 30)
    conn_id = conn.id
    await session.commit()

    provider = _provider(listed=[_posted("fresh", 1)])
    provider.get_transactions_by_date = AsyncMock(side_effect=RuntimeError("Pluggy 500"))
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    assert await _by_external_id(session, "fresh") is not None
    assert (await _by_external_id(session, "old-pending")).status == "pending"
    connection = await session.get(BankConnection, conn_id)
    assert connection is not None and connection.status == "active"


@pytest.mark.asyncio
async def test_promoted_row_survives_phantom_cleanup(
    session: AsyncSession, test_user, test_workspace,
):
    """A row the re-read still returns is not a phantom, even next to a paired
    look-alike that the regular listing returned."""
    conn, account = await _setup(session, test_user.id)
    _add_tx(session, test_user.id, account, "old-pending", 30, description="PAGAMENTO")
    _add_tx(
        session, test_user.id, account, "paired-twin", 30,
        description="PAGAMENTO", status="posted", transfer_pair_id=uuid.uuid4(),
    )
    await session.commit()

    provider = _provider(
        listed=[_posted("paired-twin", 30, description="PAGAMENTO")],
        reread=[_posted("old-pending", 30, description="PAGAMENTO")],
    )
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    row = await _by_external_id(session, "old-pending")
    assert row is not None
    assert row.status == "posted"


@pytest.mark.parametrize(
    "days_ago, listed",
    [
        (-20, False),  # a scheduled installment, pending by design
        (120, False),  # outside the re-read window
        (25, True),  # the regular listing just reported it as still pending
    ],
)
@pytest.mark.asyncio
async def test_rekeyed_match_only_promotes_the_rows_that_triggered_the_reread(
    session: AsyncSession, test_user, test_workspace, days_ago, listed,
):
    """The twin match behind a re-keyed row has no date bound, so it could
    reach pending rows the re-read was never issued for."""
    conn, account = await _setup(session, test_user.id)
    _add_tx(session, test_user.id, account, "old-pending", 30)
    other = _add_tx(
        session, test_user.id, account, "other-pending", days_ago,
        description="LOOK ALIKE",
    )
    await session.commit()

    provider = _provider(
        listed=[_posted("other-pending", days_ago, description="LOOK ALIKE", status="pending")]
        if listed else [],
        reread=[
            _posted("old-pending", 30),
            _posted("rekeyed", days_ago, description="LOOK ALIKE"),
        ],
    )
    await _sync(session, conn, test_workspace.id, test_user.id, provider)

    old = await _by_external_id(session, "old-pending")
    assert old is not None
    assert old.status == "posted"
    await session.refresh(other)
    assert other.status == "pending"
    assert other.external_id == "other-pending"
