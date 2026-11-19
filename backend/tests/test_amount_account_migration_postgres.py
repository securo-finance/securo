"""Migration 123 on real PostgreSQL: which rows get an account-currency amount."""
import importlib.util
import uuid
from datetime import date
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
    / "123_transaction_amount_account.py"
)
_SPEC = importlib.util.spec_from_file_location("amount_account_migration", _MIGRATION_PATH)
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


def _row(account: Account, user_id, currency: str, primary: str | None) -> Transaction:
    return Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account.id,
        description=f"{currency} purchase", amount=Decimal("100.00"), currency=currency,
        date=date(2025, 3, 10), type="debit", source="manual",
        amount_primary=Decimal(primary) if primary is not None else None,
    )


async def test_backfill_copies_the_figure_the_balance_reads_today(session, test_user):
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Card", type="credit_card",
        balance=Decimal("0"), currency="BRL",
    )
    session.add(account)
    await session.flush()
    foreign = _row(account, test_user.id, "USD", "545.10")
    lower_case_same = _row(account, test_user.id, "brl", "100.00")
    unconverted = _row(account, test_user.id, "EUR", None)
    session.add_all([foreign, lower_case_same, unconverted])
    await session.flush()

    schema = session.bind.get_execution_options()["schema_translate_map"][None]
    conn = await session.connection()
    # The migration's raw SQL bypasses schema_translate_map.
    await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    await conn.execute(text(migration.BACKFILL_SQL))

    stored = dict(
        (await session.execute(
            select(Transaction.id, Transaction.amount_account).where(
                Transaction.account_id == account.id
            )
        )).all()
    )
    assert stored[foreign.id] == Decimal("545.10")
    # Same currency as the account, whatever the case: balances read `amount`.
    assert stored[lower_case_same.id] is None
    # Not converted yet: stays NULL, as the balance already counted it as zero.
    assert stored[unconverted.id] is None
