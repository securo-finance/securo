"""Net worth balances on real PostgreSQL over a ten-year daily range.

PostgreSQL refuses a target list with more than 1,664 entries, and a daily
ten-year trend is one SUM column per day. ``_account_balances_at`` sends the
cutoffs in batches, and the numbers must still match the single-account read
on both sides of every batch boundary.
"""
import time
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import pytest_asyncio

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.services.dashboard_service import _account_balance_at, _account_balances_at
from app.services.report_service import get_net_worth_report

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


async def test_a_daily_ten_year_trend_answers_on_postgres(
    session, test_user, test_workspace,
):
    today = app_today()
    manual = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Manual", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    connection = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="test",
        external_id=f"pg-nw-{uuid.uuid4().hex[:8]}", institution_name="Test bank",
        credentials={}, status="active",
    )
    session.add_all([manual, connection])
    await session.flush()
    connected = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Connected", type="checking",
        balance=Decimal("3000"), currency="BRL", connection_id=connection.id,
    )
    session.add(connected)
    await session.flush()
    for account in (manual, connected):
        for amount, txn_type, days_ago in (
            (Decimal("5000"), "credit", 3600),
            (Decimal("1200"), "debit", 800),
        ):
            session.add(Transaction(
                id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
                description="wide range", amount=amount, currency="BRL",
                type=txn_type, date=today - timedelta(days=days_ago),
                source="manual", status="posted",
            ))
    await session.commit()

    cutoffs = [today - timedelta(days=days) for days in range(3653, -1, -1)]
    started = time.perf_counter()
    batched = await _account_balances_at(session, [manual, connected], cutoffs)
    elapsed = time.perf_counter() - started

    assert len(batched) == len(cutoffs) * 2, elapsed
    sampled = cutoffs[0:2] + cutoffs[498:502] + cutoffs[998:1002] + cutoffs[2998:3002] + cutoffs[-2:]
    for account in (manual, connected):
        for cutoff in sampled:
            expected = await _account_balance_at(session, account, cutoff)
            assert batched[(account.id, cutoff)] == pytest.approx(expected), (
                account.name, cutoff
            )


async def test_the_net_worth_report_answers_a_daily_ten_year_range(
    session, test_user, test_workspace,
):
    today = app_today()
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Checking", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
        description="opening", amount=Decimal("9000"), currency="BRL",
        type="credit", date=today - timedelta(days=3500), source="manual",
        status="posted",
    ))
    await session.commit()

    report = await get_net_worth_report(
        session, test_workspace.id, test_user.id, interval="daily",
        start_date=today - timedelta(days=3653), end_date=today,
    )

    assert len(report.trend) == 3654
    assert report.trend[153].value == pytest.approx(9000.0)
    assert report.trend[-1].value == pytest.approx(9000.0)
