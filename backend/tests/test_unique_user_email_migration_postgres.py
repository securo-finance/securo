"""Migration 134 on real PostgreSQL: which account keeps a shared address.

An instance that ran a release before the case-insensitive checks can hold
``ana@example.com`` and ``Ana@example.com`` as two accounts (#568). The
migration moves the extra rows aside and deactivates them, then builds the
unique index on ``lower(email)``. Which row keeps the address matters: an
administrator is the only one who can reactivate the other, so deactivating
the superuser would leave the instance with nobody able to reach the admin
screens.
"""

import importlib.util
import uuid
from pathlib import Path

import bcrypt
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.user import User

pytestmark = pytest.mark.asyncio

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "134_unique_user_email.py"
)
_SPEC = importlib.util.spec_from_file_location("unique_user_email_migration", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


async def _search_path(session) -> None:
    schema = session.bind.get_execution_options()["schema_translate_map"][None]
    conn = await session.connection()
    # The migration's raw SQL bypasses schema_translate_map.
    await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))


async def _take_the_index_down(session) -> None:
    """The models build 134's index in the test schema; the pair predates it."""
    await _search_path(session)
    await session.execute(text("DROP INDEX IF EXISTS uq_users_lower_email"))
    await session.commit()


async def _upgrade(session) -> None:
    await _search_path(session)
    conn = await session.connection()

    def run(sync_conn):
        # `op.execute` is an Alembic proxy: it answers only inside the
        # operations context this builds for the test's own connection.
        context = MigrationContext.configure(sync_conn)
        with Operations.context(context):
            migration.upgrade()

    await conn.run_sync(run)


def _account(email: str, ident: str, *, superuser: bool = False) -> User:
    return User(
        id=uuid.UUID(ident),
        email=email,
        hashed_password=bcrypt.hashpw(b"somepass123", bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=superuser,
        is_verified=True,
    )


async def _rows(session) -> list[User]:
    result = await session.execute(select(User).order_by(User.id))
    return list(result.scalars())


async def _active_superusers(session) -> int:
    return await session.scalar(text("SELECT count(*) FROM users WHERE is_superuser AND is_active"))


async def test_the_administrator_keeps_the_address(session):
    """A superuser is the only one able to reactivate the other account."""
    await _take_the_index_down(session)
    session.add(_account("Admin@example.com", "11111111-1111-1111-1111-111111111111"))
    session.add(
        _account(
            "admin@example.com",
            "99999999-9999-9999-9999-999999999999",
            superuser=True,
        )
    )
    await session.commit()

    await _upgrade(session)

    by_id = {row.id: row for row in await _rows(session)}
    admin = by_id[uuid.UUID("99999999-9999-9999-9999-999999999999")]
    plain = by_id[uuid.UUID("11111111-1111-1111-1111-111111111111")]
    assert admin.email == "admin@example.com"
    assert admin.is_active is True
    assert plain.email == "Admin+duplicate-11111111@example.com"
    assert plain.is_active is False
    assert await _active_superusers(session) == 1


async def test_between_plain_accounts_the_lowest_id_keeps_it(session):
    await _take_the_index_down(session)
    session.add(_account("ana@example.com", "11111111-1111-1111-1111-111111111111"))
    session.add(_account("Ana@example.com", "22222222-2222-2222-2222-222222222222"))
    session.add(_account("bia@example.com", "33333333-3333-3333-3333-333333333333"))
    await session.commit()

    await _upgrade(session)

    kept, moved, untouched = await _rows(session)
    assert kept.email == "ana@example.com"
    assert kept.is_active is True
    assert moved.email == "Ana+duplicate-22222222@example.com"
    assert moved.is_active is False
    assert untouched.email == "bia@example.com"
    assert untouched.is_active is True


async def test_the_index_is_built_and_a_second_row_is_refused(session):
    await _take_the_index_down(session)
    session.add(_account("ana@example.com", "11111111-1111-1111-1111-111111111111"))
    await session.commit()

    await _upgrade(session)
    await session.commit()

    indexes = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'users'")
    )
    assert "uq_users_lower_email" in {row[0] for row in indexes}

    session.add(_account("ANA@example.com", "44444444-4444-4444-4444-444444444444"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_running_it_twice_changes_nothing(session):
    await _take_the_index_down(session)
    session.add(_account("ana@example.com", "11111111-1111-1111-1111-111111111111"))
    session.add(_account("Ana@example.com", "22222222-2222-2222-2222-222222222222"))
    await session.commit()

    await _upgrade(session)
    await session.commit()
    once = [(user.email, user.is_active) for user in await _rows(session)]

    await _upgrade(session)
    await session.commit()

    assert [(user.email, user.is_active) for user in await _rows(session)] == once
