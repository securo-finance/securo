"""Migration 128 on real PostgreSQL: which rows the backfill marks paid.

A row dated today or earlier is taken as paid (the history was recorded as
done); a row dated ahead keeps the column's default, unpaid, because it has
not happened yet — a scheduled installment in 2030 is not money already moved.
"""
import importlib.util
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.models.account import Account
from app.models.transaction import Transaction

pytestmark = pytest.mark.asyncio

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "128_transaction_paid_status.py"
)
_SPEC = importlib.util.spec_from_file_location("paid_status_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user fixture starts in a fresh per-test PostgreSQL schema."""


def _row(account: Account, user_id, day: date, *, source: str = "manual") -> Transaction:
    return Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account.id,
        description=f"Row on {day.isoformat()}", amount=Decimal("10.00"),
        currency="BRL", date=day, type="debit", source=source,
        is_paid=False,
    )


async def test_backfill_marks_rows_up_to_today_paid_and_leaves_future_rows(
    session, test_user
):
    today = (await session.execute(text("SELECT CURRENT_DATE"))).scalar_one()
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Checking", type="checking",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    past = _row(account, test_user.id, today - timedelta(days=400))
    yesterday = _row(account, test_user.id, today - timedelta(days=1), source="sync")
    today_row = _row(account, test_user.id, today)
    tomorrow = _row(account, test_user.id, today + timedelta(days=1), source="recurring")
    in_2030 = _row(account, test_user.id, date(2030, 1, 10), source="manual")
    session.add_all([past, yesterday, today_row, tomorrow, in_2030])
    await session.flush()

    schema = session.bind.get_execution_options()["schema_translate_map"][None]
    conn = await session.connection()
    # The migration's raw SQL bypasses schema_translate_map.
    await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    await conn.execute(text(migration.BACKFILL_SQL))

    stored = {
        row.id: row.is_paid
        for row in (await session.execute(
            select(Transaction.id, Transaction.is_paid).where(
                Transaction.account_id == account.id
            )
        )).all()
    }
    assert stored[past.id] is True
    assert stored[yesterday.id] is True
    assert stored[today_row.id] is True
    assert stored[tomorrow.id] is False
    assert stored[in_2030.id] is False
