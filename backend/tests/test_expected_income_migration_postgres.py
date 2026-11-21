"""Migration 125 on real PostgreSQL: what the table enforces and the upsert."""
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models.expected_income import ExpectedIncome
from app.services.budget_service import set_expected_income

pytestmark = pytest.mark.asyncio

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "125_expected_incomes.py"
)
_SPEC = importlib.util.spec_from_file_location("expected_incomes_migration", _MIGRATION_PATH)
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


async def _search_path(session):
    schema = session.bind.get_execution_options()["schema_translate_map"][None]
    conn = await session.connection()
    # The migration's DDL bypasses schema_translate_map.
    await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    return conn


async def _run_operation(session, operation) -> None:
    conn = await _search_path(session)

    def run(sync_conn):
        # The migration calls alembic.op, whose proxy only exists inside an
        # Operations context (what `alembic upgrade` installs for it).
        with Operations.context(MigrationContext.configure(sync_conn)):
            operation()

    await conn.run_sync(run)


async def _drop_model_table(session) -> None:
    """Drop the table create_all built from the model so upgrade() has to make it."""
    conn = await _search_path(session)
    await conn.execute(text("DROP TABLE IF EXISTS expected_incomes CASCADE"))


async def _table_exists(session) -> bool:
    conn = await _search_path(session)
    return await conn.scalar(text("SELECT to_regclass('expected_incomes')")) is not None


async def _seed_income(session, workspace_id) -> ExpectedIncome:
    row = ExpectedIncome(
        workspace_id=workspace_id,
        month=date(2026, 4, 1),
        amount=Decimal("3450.00"),
        currency="USD",
    )
    session.add(row)
    await session.flush()
    return row


async def test_upgrade_creates_a_table_that_keeps_the_typed_income(session, test_workspace):
    await _drop_model_table(session)
    await _run_operation(session, migration.upgrade)
    assert await _table_exists(session)

    row = await _seed_income(session, test_workspace.id)
    stored = await session.scalar(
        select(ExpectedIncome).where(ExpectedIncome.id == row.id)
    )
    assert stored.month == date(2026, 4, 1)
    assert stored.amount == Decimal("3450.00")
    assert stored.currency == "USD"
    assert stored.created_at is not None


async def test_unique_refuses_a_second_income_for_the_same_workspace_month(
    session, test_workspace
):
    await _drop_model_table(session)
    await _run_operation(session, migration.upgrade)

    await _seed_income(session, test_workspace.id)
    session.add(
        ExpectedIncome(
            workspace_id=test_workspace.id,
            month=date(2026, 4, 1),
            amount=Decimal("4000.00"),
            currency="USD",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_downgrade_makes_the_table_gone(session):
    await _drop_model_table(session)
    await _run_operation(session, migration.upgrade)
    assert await _table_exists(session)

    await _run_operation(session, migration.downgrade)
    assert not await _table_exists(session)


async def test_the_postgres_upsert_keeps_one_row_and_the_last_values(
    session, test_workspace
):
    month = date(2026, 4, 1)
    await set_expected_income(
        session, test_workspace.id, month, Decimal("3450.00"), "USD", reader_currency="USD"
    )
    await set_expected_income(
        session, test_workspace.id, month, Decimal("4000.55"), "EUR", reader_currency="EUR"
    )

    rows = (
        await session.execute(
            select(ExpectedIncome).where(ExpectedIncome.workspace_id == test_workspace.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].amount == Decimal("4000.55")
    assert rows[0].currency == "EUR"

    cleared = await set_expected_income(
        session, test_workspace.id, month, None, "EUR", reader_currency="EUR"
    )
    assert cleared.amount is None
    assert (
        await session.scalar(
            select(ExpectedIncome).where(ExpectedIncome.workspace_id == test_workspace.id)
        )
    ) is None
