"""Migration 126 on real PostgreSQL, also on a database that ran #1778's branch.

That branch shipped this migration as revision 125, so such a database has
`import_logs.ofx_utc_dates` already and is stamped 125 without the budget
currency backfill (#412).
"""
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

from app.models.budget import Budget
from app.models.import_log import ImportLog

pytestmark = pytest.mark.asyncio

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "126_import_log_ofx_utc_dates.py"
)
_SPEC = importlib.util.spec_from_file_location("import_log_ofx_migration", _MIGRATION_PATH)
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


async def _seed(session, test_user, test_workspace, test_categories) -> None:
    session.add_all([
        ImportLog(
            user_id=test_user.id, workspace_id=test_workspace.id,
            filename=f"r412.{fmt}", format=fmt, transaction_count=1,
            ofx_utc_dates=False,
        )
        for fmt in ("OFX", "csv")
    ])
    # A stale column default: the owner of this budget displays BRL.
    session.add(Budget(
        user_id=test_user.id, workspace_id=test_workspace.id,
        category_id=test_categories[0].id, amount=Decimal("1800"),
        month=date(2025, 3, 1), currency="EUR",
    ))
    await session.flush()


async def _upgrade(session, *, drop_column: bool) -> None:
    schema = session.bind.get_execution_options()["schema_translate_map"][None]
    conn = await session.connection()

    def upgrade(sync_conn) -> None:
        # The migration's raw SQL bypasses schema_translate_map.
        sync_conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        if drop_column:
            sync_conn.exec_driver_sql("ALTER TABLE import_logs DROP COLUMN ofx_utc_dates")
        with Operations.context(MigrationContext.configure(sync_conn)):
            migration.upgrade()

    await conn.run_sync(upgrade)


async def _marks(session) -> dict[str, bool]:
    rows = await session.execute(text("SELECT format, ofx_utc_dates FROM import_logs"))
    return dict(rows.all())


async def test_adds_the_column_and_marks_existing_ofx_imports(
    session, test_user, test_workspace, test_categories,
):
    await _seed(session, test_user, test_workspace, test_categories)

    await _upgrade(session, drop_column=True)

    assert await _marks(session) == {"OFX": True, "csv": False}
    # Migration 125 ran before it on this database; 126 leaves budgets alone.
    assert (await session.execute(select(Budget.currency))).scalar_one() == "EUR"


async def test_database_from_the_1778_branch_upgrades_and_gets_budget_currency(
    session, test_user, test_workspace, test_categories,
):
    # The column is there already, as #1778's own 125 left it.
    await _seed(session, test_user, test_workspace, test_categories)

    await _upgrade(session, drop_column=False)

    # Imports made since that branch were parsed the new way: not re-marked.
    assert await _marks(session) == {"OFX": False, "csv": False}
    # The budget backfill this database skipped runs here instead.
    assert (await session.execute(select(Budget.currency))).scalar_one() == "BRL"
