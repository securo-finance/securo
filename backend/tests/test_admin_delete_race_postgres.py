"""Two deletions of the same account: one deletes, the other finds nothing (#557).

``delete_user`` read the account and then wrote its record. Two requests
arriving together both found it, both wrote a record pointing at it, and the
loser died on the record's foreign key with a 500 — a regression against main,
where the same six parallel clicks all answered 204.

Real PostgreSQL, one session per request. The read is the thing that races,
so each request runs the service exactly as the route does and the answer is
what the API would return: 204 for the one that deleted, 404 for the rest.
"""
import asyncio
import uuid

import bcrypt
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_access_log import AdminAccessLog
from app.models.user import User
from app.services import admin_service

pytestmark = pytest.mark.asyncio

REASON = "Duplicate deletion from two support tabs"
CALLERS = 6


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


async def test_six_parallel_deletions_of_one_account_all_answer(
    session: AsyncSession, postgres_sessions
):
    victim = await _add_user(session, "race@example.com")
    actor = await _add_user(session, "actor@example.com")
    victim_id, actor_id = victim.id, actor.id

    async def delete():
        async with postgres_sessions() as request_session:
            try:
                return await admin_service.delete_user(
                    request_session, victim_id, actor_id, reason=REASON
                )
            except Exception as exc:  # noqa: BLE001 - the failure is the finding
                return exc

    answers = await asyncio.wait_for(
        asyncio.gather(*(delete() for _ in range(CALLERS))), timeout=60
    )

    failures = [answer for answer in answers if isinstance(answer, Exception)]
    assert failures == [], f"a parallel deletion failed: {failures!r}"
    # One request deletes; every other one finds the account already gone.
    assert sorted(answer for answer in answers) == [False] * (CALLERS - 1) + [True]

    async with postgres_sessions() as check:
        assert (
            await check.execute(select(User).where(User.id == victim_id))
        ).scalar_one_or_none() is None
        # One record of the deletion, and it is readable even though the
        # account it names is gone.
        records = (
            await check.execute(
                select(AdminAccessLog).where(AdminAccessLog.action == "user.delete")
            )
        ).scalars().all()
        assert len(records) == 1
        assert records[0].target_user_id is None
        assert records[0].target_email == "race@example.com"
        total = (
            await check.execute(select(func.count()).select_from(AdminAccessLog))
        ).scalar()
        assert total == 1
