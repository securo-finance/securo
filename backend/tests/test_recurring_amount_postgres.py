"""A refused or concurrent bill edit never leaves a wrong amount (PostgreSQL).

The on-demand rate fetch commits the session it runs on, and two edits of one
bill can overlap; neither shows on the SQLite fixtures.
"""
import asyncio
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text

from app.core.app_clock import app_today
from app.core.money import MoneyOutOfRange
from app.models.fx_rate import FxRate
from app.models.recurring_transaction import RecurringTransaction
from app.schemas.recurring_transaction import (
    RecurringTransactionCreate,
    RecurringTransactionUpdate,
)
from app.services import fx_rate_service
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    update_recurring_transaction,
)

pytestmark = pytest.mark.asyncio

TOO_BIG = Decimal("2000000000000")  # 2e12 USD is 1e13 BRL at 5


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused fixtures start in a fresh per-test PostgreSQL schema."""


@pytest.fixture
def provider_rate():
    async def fetch(provider, target):
        return {"BRL": Decimal("5")}

    with patch.object(fx_rate_service, "_fetch", fetch):
        yield


def _bill(account, amount: Decimal = Decimal("100"), currency: str = "BRL"):
    return RecurringTransactionCreate(
        description="Bill", amount=amount, currency=currency, type="debit",
        frequency="monthly", start_date=app_today(), account_id=account.id,
    )


async def test_a_refused_create_is_not_saved_by_the_rate_fetch(
    session, postgres_sessions, test_user, test_workspace, test_account, provider_rate
):
    with pytest.raises(MoneyOutOfRange):
        await create_recurring_transaction(
            session, test_workspace.id, test_user.id,
            _bill(test_account, amount=TOO_BIG, currency="USD"),
        )
    await session.rollback()

    async with postgres_sessions() as other:
        assert await other.scalar(select(func.count(RecurringTransaction.id))) == 0
        # The fetch ran and stored its rate, so the refusal came after it.
        assert await other.scalar(select(func.count(FxRate.id))) == 1


async def test_a_refused_edit_is_not_saved_by_the_rate_fetch(
    session, postgres_sessions, test_user, test_workspace, test_account, provider_rate
):
    bill = await create_recurring_transaction(
        session, test_workspace.id, test_user.id, _bill(test_account)
    )
    bill_id = bill.id

    with pytest.raises(MoneyOutOfRange):
        await update_recurring_transaction(
            session, bill_id, test_workspace.id,
            RecurringTransactionUpdate(amount=TOO_BIG, currency="USD"),
        )
    await session.rollback()

    async with postgres_sessions() as other:
        stored = await other.get(RecurringTransaction, bill_id)
        assert stored is not None
        assert (stored.amount, stored.currency, stored.amount_primary) == (
            Decimal("100.00"), "BRL", Decimal("100.00")
        )
        assert await other.scalar(select(func.count(FxRate.id))) == 1


async def _until_blocked(sessions) -> None:
    """Wait until some session waits for a row lock."""
    async with sessions() as probe:
        for _ in range(200):
            waiting = await probe.scalar(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                "AND query LIKE '%recurring_transactions%'"
            ))
            # Each read of the activity view needs its own transaction.
            await probe.rollback()
            if waiting:
                return
            await asyncio.sleep(0.02)
    pytest.fail("the second edit never waited for the first")


async def test_an_edit_converts_the_amount_a_concurrent_edit_committed(
    session, postgres_sessions, test_user, test_workspace, test_account
):
    session.add(FxRate(
        base_currency="USD", quote_currency="BRL", date=app_today(),
        rate=Decimal("5"), source="test",
    ))
    bill = await create_recurring_transaction(
        session, test_workspace.id, test_user.id, _bill(test_account, currency="USD")
    )
    assert bill.amount_primary == Decimal("500.00")

    async def edit_currency():
        async with postgres_sessions() as other:
            await update_recurring_transaction(
                other, bill.id, test_workspace.id, RecurringTransactionUpdate(currency="BRL")
            )

    async with postgres_sessions() as first:
        row = await first.get(RecurringTransaction, bill.id)
        assert row is not None
        row.amount, row.amount_primary = Decimal("200"), Decimal("1000")
        await first.flush()
        second = asyncio.create_task(edit_currency())
        try:
            await _until_blocked(postgres_sessions)
        finally:
            await first.commit()
            await second

    async with postgres_sessions() as reader:
        stored = await reader.get(RecurringTransaction, bill.id)
        assert stored is not None
        assert (stored.amount, stored.currency, stored.amount_primary) == (
            Decimal("200.00"), "BRL", Decimal("200.00")
        )
