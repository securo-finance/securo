"""Fast fixtures must retain committed-data isolation and real password checks."""

import sqlite3
import subprocess
import sys
from inspect import unwrap

import argon2
import bcrypt
import pytest
from fastapi_users import password
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.database import Base
from app.models.app_settings import AppSetting
from app.models.transaction import Transaction
from app.models.user import User
from tests.conftest import TestSessionLocal, clean_db


async def test_cleanup_removes_independently_committed_rows_and_keeps_schema(
    session, test_transactions
):
    tables = tuple(Base.metadata.sorted_tables)
    for key in ("first-session", "second-session"):
        async with TestSessionLocal() as writer:
            writer.add(AppSetting(key=key, value="synthetic"))
            await writer.commit()

    await unwrap(clean_db)(session)

    async with TestSessionLocal() as observer:
        for table in tables:
            assert await observer.scalar(select(func.count()).select_from(table)) == 0
        observer.add(AppSetting(key="schema-still-usable", value="synthetic"))
        await observer.commit()
    assert tuple(Base.metadata.sorted_tables) == tables


async def test_failed_cleanup_rolls_back_earlier_deletes(session, test_transactions):
    before = len(test_transactions)
    await session.execute(text(
        "CREATE TEMP TRIGGER fail_cleanup BEFORE DELETE ON users "
        "BEGIN SELECT RAISE(ABORT, 'synthetic cleanup failure'); END"
    ))
    await session.commit()
    try:
        with pytest.raises((sqlite3.IntegrityError, IntegrityError), match="cleanup failure"):
            await unwrap(clean_db)(session)
        await session.rollback()
        async with TestSessionLocal() as observer:
            assert await observer.scalar(select(func.count()).select_from(Transaction)) == before
    finally:
        await session.execute(text("DROP TRIGGER fail_cleanup"))
        await session.commit()
    await unwrap(clean_db)(session)
    assert await session.scalar(select(func.count()).select_from(Transaction)) == 0


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted", "flushed"])
async def test_cleanup_rejects_pending_state_without_committing_it(session, test_user, pending):
    user_id, email = test_user.id, test_user.email
    if pending in {"new", "flushed"}:
        session.add(AppSetting(key="pending-row", value="synthetic"))
        if pending == "flushed":
            await session.flush()
    elif pending == "dirty":
        test_user.email = "changed@example.test"
    else:
        await session.delete(test_user)

    with pytest.raises(AssertionError, match="fresh session"):
        await unwrap(clean_db)(session)
    await session.rollback()

    async with TestSessionLocal() as observer:
        user = await observer.get(User, user_id)
        assert user is not None and user.email == email
        assert await observer.get(AppSetting, "pending-row") is None
    await unwrap(clean_db)(session)


def test_password_fixture_preserves_verification_upgrade_and_production_defaults():
    helper = password.PasswordHelper()
    hashed = helper.hash("synthetic-password")
    parameters = argon2.extract_parameters(hashed)
    assert (parameters.time_cost, parameters.memory_cost, parameters.parallelism) == (1, 8, 1)
    assert parameters.hash_len == argon2.DEFAULT_HASH_LENGTH
    assert parameters.salt_len == argon2.DEFAULT_RANDOM_SALT_LENGTH
    assert helper.verify_and_update("synthetic-password", hashed) == (True, None)
    assert helper.verify_and_update("wrong-password", hashed) == (False, None)

    assert bcrypt.gensalt().startswith(b"$2b$04$")
    assert bcrypt.gensalt(5).startswith(b"$2b$05$")
    assert bcrypt.gensalt(rounds=5, prefix=b"2a").startswith(b"$2a$05$")
    assert bcrypt.gensalt(prefix=b"2a").startswith(b"$2a$12$")
    legacy = bcrypt.hashpw(b"synthetic-password", bcrypt.gensalt()).decode()
    valid, upgraded = helper.verify_and_update("synthetic-password", legacy)
    assert valid and upgraded is not None and upgraded.startswith("$argon2id$")
    assert helper.verify_and_update("synthetic-password", upgraded) == (True, None)
    assert helper.verify_and_update("wrong-password", legacy) == (False, None)

    explicit = password.Argon2Hasher(time_cost=2, memory_cost=32, parallelism=2)
    parameters = argon2.extract_parameters(explicit.hash("synthetic-password"))
    assert (parameters.time_cost, parameters.memory_cost, parameters.parallelism) == (2, 32, 2)
    positional = password.Argon2Hasher(2, 32, 2)
    parameters = argon2.extract_parameters(positional.hash("synthetic-password"))
    assert (parameters.time_cost, parameters.memory_cost, parameters.parallelism) == (2, 32, 2)
    partial_keywords = password.Argon2Hasher(time_cost=2)
    parameters = argon2.extract_parameters(partial_keywords.hash("synthetic-password"))
    assert parameters.time_cost == 2
    assert parameters.memory_cost == argon2.DEFAULT_MEMORY_COST
    assert parameters.parallelism == argon2.DEFAULT_PARALLELISM
    subprocess.run(
        [sys.executable, "-c", """
import argon2
import bcrypt
from fastapi_users.password import PasswordHelper
assert bcrypt.gensalt().startswith(b"$2b$12$")
helper = PasswordHelper()
hashed = helper.hash('synthetic-password')
parameters = argon2.extract_parameters(hashed)
assert parameters.time_cost == argon2.DEFAULT_TIME_COST
assert parameters.memory_cost == argon2.DEFAULT_MEMORY_COST
assert parameters.parallelism == argon2.DEFAULT_PARALLELISM
assert helper.verify_and_update('synthetic-password', hashed) == (True, None)
assert helper.verify_and_update('wrong-password', hashed) == (False, None)
"""],
        check=True,
    )
