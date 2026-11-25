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
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import bcrypt
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.api.users_me import update_me
from app.core.auth import UserManager, _AccountLookupUserDatabase
from app.models.admin_access_log import AdminAccessLog
from app.models.user import User
from app.schemas.admin import AdminUserUpdate
from app.schemas.user import UserUpdate
from app.services import account_email_service, admin_service

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


def _request() -> Request:
    """The least a request needs for the route's own use of it."""
    return Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/api/users/me",
            "headers": [],
            "query_string": b"",
        }
    )


@asynccontextmanager
async def _deletion_holding_the_row(postgres_sessions, monkeypatch, victim_id, actor_id):
    """A deletion of the victim, started and stopped while it owns the row.

    It takes the row at its start and only lets go when the test asks; the
    other call, arriving meanwhile, waits on that lock and then finds the
    account gone.
    """
    reached = asyncio.Event()
    let_it_go = asyncio.Event()
    record = admin_service._record_admin_access

    async def paused_record(*args, **kwargs):
        """The deletion stopped after its deletes, before its commit."""
        reached.set()
        await let_it_go.wait()
        return await record(*args, **kwargs)

    monkeypatch.setattr(admin_service, "_record_admin_access", paused_record)

    async def delete():
        async with postgres_sessions() as request_session:
            return await admin_service.delete_user(
                request_session, victim_id, actor_id, reason=REASON
            )

    deletion = asyncio.create_task(delete())
    await asyncio.wait_for(reached.wait(), timeout=10)
    try:
        yield let_it_go.set
    finally:
        let_it_go.set()
        await asyncio.wait_for(deletion, timeout=30)


@asynccontextmanager
async def _owner_session_user(postgres_sessions, victim_id):
    """The account as its owner's request loads it, in that request's session."""
    async with postgres_sessions() as owner_session:
        owner = (
            await owner_session.execute(select(User).where(User.id == victim_id))
        ).scalar_one()
        yield owner_session, owner


async def test_an_acceptance_arriving_while_the_account_is_deleted_says_so(
    session: AsyncSession, postgres_sessions, monkeypatch
):
    """The owner accepts the address; the administrator deletes the account.

    The acceptance waits for the lock the deletion holds, then finds nothing
    left. Reading the row with `scalar_one` made that a 500 in four rounds of
    twelve (#567); it answers "this account no longer exists" instead.
    """
    victim = await _add_user(session, "accept-race@example.com")
    actor = await _add_user(session, "accept-actor@example.com")
    victim.pending_email = "moved@example.com"
    victim.pending_email_requested_at = datetime.now(timezone.utc)
    await session.commit()
    victim_id, actor_id = victim.id, actor.id

    async def accept():
        async with _owner_session_user(postgres_sessions, victim_id) as (owner_session, owner):
            return await account_email_service.confirm_pending_email(
                owner_session, owner, expected_email="moved@example.com"
            )

    async with _deletion_holding_the_row(
        postgres_sessions, monkeypatch, victim_id, actor_id
    ) as let_it_go:
        accepting = asyncio.create_task(accept())
        await asyncio.sleep(0.5)
        let_it_go()
        with pytest.raises(account_email_service.AccountGone):
            await asyncio.wait_for(accepting, timeout=30)


async def test_a_decline_arriving_while_the_account_is_deleted_says_so(
    session: AsyncSession, postgres_sessions, monkeypatch
):
    """The same race through the decline, which used to fail at its commit."""
    victim = await _add_user(session, "decline-race@example.com")
    actor = await _add_user(session, "decline-actor@example.com")
    victim.pending_email = "moved@example.com"
    victim.pending_email_requested_at = datetime.now(timezone.utc)
    await session.commit()
    victim_id, actor_id = victim.id, actor.id

    async def decline():
        async with _owner_session_user(postgres_sessions, victim_id) as (owner_session, owner):
            return await account_email_service.cancel_pending_email(owner_session, owner)

    async with _deletion_holding_the_row(
        postgres_sessions, monkeypatch, victim_id, actor_id
    ) as let_it_go:
        declining = asyncio.create_task(decline())
        await asyncio.sleep(0.5)
        let_it_go()
        with pytest.raises(account_email_service.AccountGone):
            await asyncio.wait_for(declining, timeout=30)


async def test_a_profile_edit_arriving_while_the_account_is_deleted_says_so(
    session: AsyncSession, postgres_sessions, monkeypatch
):
    """`PATCH /api/users/me` while the administrator deletes the same account.

    The library's route wrote the account it loaded before the deletion
    committed, and SQLAlchemy answered with `StaleDataError`: a 500 in two
    rounds of twelve (#567). The route takes the row first and answers 404.
    """
    victim = await _add_user(session, "me-race@example.com")
    actor = await _add_user(session, "me-actor@example.com")
    victim_id, actor_id = victim.id, actor.id

    async def edit():
        async with _owner_session_user(postgres_sessions, victim_id) as (owner_session, owner):
            manager = UserManager(_AccountLookupUserDatabase(owner_session, User))
            return await update_me(
                request=_request(),
                user_update=UserUpdate(theme="ocean"),
                user=owner,
                session=owner_session,
                user_manager=manager,
            )

    async with _deletion_holding_the_row(
        postgres_sessions, monkeypatch, victim_id, actor_id
    ) as let_it_go:
        editing = asyncio.create_task(edit())
        await asyncio.sleep(0.5)
        let_it_go()
        with pytest.raises(HTTPException) as refusal:
            await asyncio.wait_for(editing, timeout=30)

    assert refusal.value.status_code == 404
