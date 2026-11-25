"""Changing and deleting the same account at once: no 500 on either (#567).

``update_user`` read the account and only later wrote the record of the
change. A deletion arriving in between committed first, so the update's record
pointed at an account that was already gone and its foreign key failed: the
PATCH answered 500 in four rounds of four, against main where the same clicks
answered 200 or 404. The update now takes the row with a lock before it reads,
as the deletion already did, so the two queue: whichever goes first makes the
other answer "not found" instead of failing.

Real PostgreSQL, one session per request, each one running the service the way
the route does. The two calls are held at the exact point that failed, so the
test fails on the old code instead of hoping the race shows up: the update is
paused right before it writes its record, the deletion runs to the end, and
only then is the update let go.
"""
import asyncio
import uuid

import bcrypt
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_access_log import AdminAccessLog
from app.models.user import User
from app.schemas.admin import AdminUserUpdate
from app.services import admin_service

pytestmark = pytest.mark.asyncio

REASON = "Two support tabs on the same customer"


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """Each test starts in a fresh per-test PostgreSQL schema."""


async def _add_user(session: AsyncSession, email: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=bcrypt.hashpw(b"somepass123", bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    return user


async def test_an_update_and_a_deletion_of_one_account_never_fail(
    session: AsyncSession, postgres_sessions, monkeypatch
):
    victim = await _add_user(session, "race@example.com")
    actor = await _add_user(session, "actor@example.com")
    victim_id, actor_id = victim.id, actor.id

    reached_the_record = asyncio.Event()
    let_it_write = asyncio.Event()
    record = admin_service._record_admin_access

    async def paused_record(*args, **kwargs):
        """The update stopped between reading the account and writing about it."""
        reached_the_record.set()
        await let_it_write.wait()
        return await record(*args, **kwargs)

    monkeypatch.setattr(admin_service, "_record_admin_access", paused_record)

    async def update():
        async with postgres_sessions() as request_session:
            return await admin_service.update_user(
                request_session,
                victim_id,
                AdminUserUpdate(reason=REASON, is_active=True),
                actor_id,
            )

    async def delete():
        async with postgres_sessions() as request_session:
            return await admin_service.delete_user(
                request_session, victim_id, actor_id, reason=REASON
            )

    update_task = asyncio.create_task(update())
    await asyncio.wait_for(reached_the_record.wait(), timeout=10)

    # The deletion arrives while the update is stopped. On the old code it runs
    # to the end and commits, which is what broke the update's record; with the
    # row taken by the update it waits, and the test lets the update go.
    delete_task = asyncio.create_task(delete())
    try:
        await asyncio.wait_for(asyncio.shield(delete_task), timeout=2)
    except asyncio.TimeoutError:
        pass
    let_it_write.set()

    changed, deleted = await asyncio.wait_for(
        asyncio.gather(update_task, delete_task), timeout=30
    )

    # Each call either did its work or found the account already gone.
    assert changed is None or isinstance(changed, User)
    assert isinstance(deleted, bool)

    async with postgres_sessions() as check:
        assert (
            await check.execute(select(User).where(User.id == victim_id))
        ).scalar_one_or_none() is None
        records = (
            await check.execute(
                select(AdminAccessLog).order_by(AdminAccessLog.created_at)
            )
        ).scalars().all()
        # One record for the update that found the account, one for the
        # deletion; neither keeps pointing at an account that no longer exists.
        assert all(record.target_user_id is None for record in records)
        assert {record.action for record in records} <= {"user.update", "user.delete"}
