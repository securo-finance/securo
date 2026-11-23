"""Migration 125 on real PostgreSQL: each budget takes its owner's currency."""
import importlib.util
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select

from app.models.budget import Budget
from app.models.user import User

pytestmark = pytest.mark.asyncio

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "125_budget_currency_from_owner.py"
)
_SPEC = importlib.util.spec_from_file_location("budget_currency_migration", _MIGRATION_PATH)
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


async def test_each_budget_takes_its_owner_display_currency(
    session, test_user, test_workspace, test_categories,
):
    no_choice = User(
        id=uuid.uuid4(), email="nochoice@example.com", hashed_password="x",
        is_active=True, is_superuser=False, is_verified=True, preferences={},
    )
    session.add(no_choice)
    await session.flush()
    # Both rows carry a stale column default, whatever their owner used.
    session.add_all([
        Budget(
            user_id=owner.id, workspace_id=test_workspace.id,
            category_id=test_categories[0].id, amount=Decimal("1800"),
            month=date(2025, 3, 1), currency="EUR",
        )
        for owner in (test_user, no_choice)
    ])
    await session.flush()

    schema = session.bind.get_execution_options()["schema_translate_map"][None]
    conn = await session.connection()

    def upgrade(sync_conn) -> None:
        # The migration's raw SQL bypasses schema_translate_map.
        sync_conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        with Operations.context(MigrationContext.configure(sync_conn)):
            migration.upgrade()

    # The instance default is USD, the fallback for an owner with no choice.
    await conn.run_sync(upgrade)

    stored = dict((await session.execute(select(Budget.user_id, Budget.currency))).all())
    assert stored == {test_user.id: "BRL", no_choice.id: "USD"}
