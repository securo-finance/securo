"""The net worth trend reads each balance once, not once per snapshot.

Issue #584: a 12-month net worth report over a handful of accounts ran the same
per-account sum 56 times, once for every account and every snapshot.
``_account_balances_at`` answers all the (account, cutoff) pairs a report needs
in a few grouped queries, and has to return exactly the number the
single-account read returns, for manual and connected accounts alike, before
and after today.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.services.dashboard_service import _account_balance_at, _account_balances_at
from app.services.report_service import get_net_worth_report


async def _make_account(session, user_id, name, *, currency="BRL", balance="0",
                        acct_type="checking", connection_id=None,
                        shared_balance_group=None):
    account = Account(
        id=uuid.uuid4(), user_id=user_id, name=name, type=acct_type,
        balance=Decimal(balance), currency=currency, connection_id=connection_id,
        shared_balance_group=shared_balance_group,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def _add_txn(session, user_id, account_id, amount, txn_type, txn_date, *,
                   currency="BRL", status="posted", is_ignored=False):
    txn = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id,
        description=f"Test {txn_type}", amount=Decimal(str(amount)),
        date=txn_date, type=txn_type, source="manual", currency=currency,
        status=status, is_ignored=is_ignored,
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    return txn


async def _make_connection(session, user_id, external_id):
    conn = BankConnection(
        id=uuid.uuid4(), user_id=user_id, provider="test",
        external_id=external_id, institution_name="Test bank",
        credentials={}, status="active",
        last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    await session.flush()
    return conn


@pytest.mark.asyncio
async def test_batched_balances_match_the_single_account_read(
    session: AsyncSession, test_user, test_workspace,
):
    """Every pair answers what ``_account_balance_at`` would, cutoffs after
    today included: those keep today's balance instead of reading ahead."""
    today = date.today()
    manual = await _make_account(session, test_user.id, "Manual")
    await _add_txn(session, test_user.id, manual.id, 500, "credit", today - timedelta(days=80))
    await _add_txn(session, test_user.id, manual.id, 200, "debit", today - timedelta(days=40))
    await _add_txn(session, test_user.id, manual.id, 100, "debit", today - timedelta(days=5))
    # Ignored rows never count in a balance.
    await _add_txn(
        session, test_user.id, manual.id, 999, "credit", today - timedelta(days=30),
        is_ignored=True,
    )
    # A future row stays out of the balance taken today.
    await _add_txn(session, test_user.id, manual.id, 700, "credit", today + timedelta(days=20))

    conn = await _make_connection(session, test_user.id, "ext-nw-batch")
    connected = await _make_account(
        session, test_user.id, "Connected", balance="1000", connection_id=conn.id,
    )
    await _add_txn(session, test_user.id, connected.id, 150, "debit", today - timedelta(days=60))
    await _add_txn(
        session, test_user.id, connected.id, 50, "debit", today - timedelta(days=10),
        status="pending",
    )
    card = await _make_account(
        session, test_user.id, "Card", balance="300", acct_type="credit_card",
        connection_id=conn.id,
    )
    await _add_txn(session, test_user.id, card.id, 40, "debit", today - timedelta(days=15))

    accounts = [manual, connected, card]
    cutoffs = [
        today - timedelta(days=90),
        today - timedelta(days=50),
        today - timedelta(days=1),
        today,
        today + timedelta(days=10),
    ]

    batched = await _account_balances_at(session, accounts, cutoffs)

    for account in accounts:
        for cutoff in cutoffs:
            expected = await _account_balance_at(session, account, cutoff)
            assert batched[(account.id, cutoff)] == pytest.approx(expected), (
                account.name, cutoff
            )


@pytest.mark.asyncio
async def test_net_worth_report_takes_every_balance_from_the_batch(
    session: AsyncSession, test_user, test_workspace,
):
    """The report values do not change, and no snapshot reads a balance alone.

    The same report is built twice: first with the batch disabled, which is the
    per-snapshot path the report used before, then with it on and the
    single-account read turned into a failure. Both must give the same trend,
    and the second must not touch the single-account read at all.
    """
    today = date.today()
    checking = await _make_account(session, test_user.id, "Checking")
    await _add_txn(session, test_user.id, checking.id, 4000, "credit", today - timedelta(days=120))
    await _add_txn(session, test_user.id, checking.id, 900, "debit", today - timedelta(days=70))
    await _add_txn(session, test_user.id, checking.id, 300, "debit", today - timedelta(days=20))

    savings = await _make_account(session, test_user.id, "Savings")
    await _add_txn(session, test_user.id, savings.id, 2500, "credit", today - timedelta(days=100))
    await _add_txn(session, test_user.id, savings.id, 500, "debit", today - timedelta(days=35))

    conn = await _make_connection(session, test_user.id, "ext-nw-report")
    card = await _make_account(
        session, test_user.id, "Card", balance="600", acct_type="credit_card",
        connection_id=conn.id,
    )
    await _add_txn(session, test_user.id, card.id, 120, "debit", today - timedelta(days=25))

    with patch(
        "app.services.report_service._account_balances_at",
        new=AsyncMock(return_value={}),
    ):
        before = await get_net_worth_report(
            session, test_workspace.id, test_user.id, months=6, interval="monthly",
        )

    with patch(
        "app.services.report_service._account_balance_at",
        new=AsyncMock(side_effect=AssertionError("read a balance per snapshot")),
    ):
        after = await get_net_worth_report(
            session, test_workspace.id, test_user.id, months=6, interval="monthly",
        )

    assert len(before.trend) == len(after.trend) >= 2
    for before_point, after_point in zip(before.trend, after.trend):
        assert after_point.date == before_point.date
        assert after_point.value == before_point.value
        assert after_point.breakdowns == before_point.breakdowns
    assert before.summary.primary_value == after.summary.primary_value


@pytest.mark.asyncio
async def test_net_worth_report_counts_a_shared_credit_line_once_from_the_batch(
    session: AsyncSession, test_user, test_workspace,
):
    """The cache carries today's shared debt too: both cards share one line,
    and the report still counts the debt once."""
    conn = await _make_connection(session, test_user.id, "ext-nw-shared")
    for name in ("Visa", "Mastercard"):
        await _make_account(
            session, test_user.id, name, balance="1000", acct_type="credit_card",
            connection_id=conn.id,
            shared_balance_group=f"{conn.id}:shared-credit",
        )

    checking = await _make_account(session, test_user.id, "Checking")
    await _add_txn(session, test_user.id, checking.id, 5000, "credit", date.today())

    report = await get_net_worth_report(
        session, test_workspace.id, test_user.id, months=3, interval="monthly",
    )

    assert report.trend[-1].breakdowns["liabilities"] == pytest.approx(1000.0)
    assert report.trend[-1].value == pytest.approx(4000.0)
