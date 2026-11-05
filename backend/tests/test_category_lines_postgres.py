"""Category lines on real PostgreSQL: foreign keys and migration 103.

SQLite test databases do not enforce foreign keys, so the cascade on raw
deletes, the block on deleting a category still used by a line, and the
migration's rollback are checked here.
"""

import importlib.util
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.transaction_category_line import TransactionCategoryLine
from app.schemas.transaction_category_line import (
    TransactionCategoryLineInput,
    TransactionCategoryLinesInput,
)
from app.services import category_line_service

pytestmark = pytest.mark.asyncio

(_MIGRATION_PATH,) = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions"
).glob("*_transaction_category_lines.py")
_SPEC = importlib.util.spec_from_file_location("category_lines_migration", _MIGRATION_PATH)
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


async def _split_transaction(session, user, workspace):
    food = Category(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name="Food")
    home = Category(id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name="Home")
    account = Account(
        user_id=user.id, workspace_id=workspace.id, name="Checking", type="checking",
        balance=0, currency="USD",
    )
    session.add_all([food, home, account])
    await session.flush()
    tx = Transaction(
        user_id=user.id, workspace_id=workspace.id, account_id=account.id,
        description="Supermarket", amount=Decimal("150"), currency="USD",
        date=date(2026, 10, 10), effective_date=date(2026, 10, 10), type="debit",
        source="manual", status="posted",
    )
    session.add(tx)
    await session.flush()
    await category_line_service.replace_lines(
        session, tx,
        TransactionCategoryLinesInput(
            share_type="exact",
            lines=[
                TransactionCategoryLineInput(category_id=food.id, amount=Decimal("40")),
                TransactionCategoryLineInput(category_id=home.id, amount=Decimal("110")),
            ],
        ),
    )
    await session.commit()
    return tx, food, home


async def _line_count(session) -> int:
    return int(await session.scalar(select(func.count()).select_from(TransactionCategoryLine)))


async def test_a_raw_delete_cascades_to_the_lines(session, test_user, test_workspace):
    tx, _, _ = await _split_transaction(session, test_user, test_workspace)
    assert await _line_count(session) == 2

    await session.execute(delete(Transaction).where(Transaction.id == tx.id))
    await session.commit()

    assert await _line_count(session) == 0


async def test_a_category_used_by_a_line_cannot_be_deleted(session, test_user, test_workspace):
    _, _, home = await _split_transaction(session, test_user, test_workspace)

    with pytest.raises(IntegrityError):
        await session.execute(delete(Category).where(Category.id == home.id))
        await session.commit()


async def test_downgrade_gives_back_the_largest_line_category(
    session, test_user, test_workspace
):
    tx, _, home = await _split_transaction(session, test_user, test_workspace)
    schema = session.bind.get_execution_options()["schema_translate_map"][None]

    def run(sync_conn, direction):
        # The migration's raw SQL is not schema-translated; point it at the
        # test schema the way a real database's search_path would.
        sync_conn.exec_driver_sql(f'SET search_path TO "{schema}"')
        with Operations.context(MigrationContext.configure(sync_conn)):
            getattr(migration, direction)()

    conn = await session.connection()
    await conn.run_sync(run, "downgrade")
    category_id = (
        await conn.execute(text("SELECT category_id FROM transactions WHERE id = :id"), {"id": tx.id})
    ).scalar_one()
    assert category_id == home.id

    # And upgrading again rebuilds the schema without touching that category.
    await conn.run_sync(run, "upgrade")
    flag = (
        await conn.execute(
            text("SELECT has_category_lines FROM transactions WHERE id = :id"), {"id": tx.id}
        )
    ).scalar_one()
    assert flag is False
