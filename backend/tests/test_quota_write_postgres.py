"""Two operators writing an account's first limits at once (ADR 0049, issue #1983).

`set_quota` used to read the row and create it when there was none. Two
`PUT /api/operator/quotas/{user_id}` for an account with no row both read
nothing, both insert, and the second fails on the primary key: a 500. The write
is one upsert, so the second waits for the first and then updates the row.
"""
import asyncio
import uuid

from sqlalchemy import text

from app.models.user import User
from app.services import quota_service

#: Whether the backend `pid` is waiting on a lock it has not been granted.
_WAITING = text("SELECT count(*) FROM pg_locks WHERE pid = :pid AND NOT granted")


async def _wait_until_blocked(session, pid: int, timeout: float = 15.0) -> None:
    """Wait until backend `pid` is blocked on a lock, or fail.

    The first write must stay uncommitted until the second has really reached
    the row, so the test shows the two colliding and not one running after the
    other.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not await session.scalar(_WAITING, {"pid": pid}):
        if loop.time() >= deadline:
            raise AssertionError(f"backend {pid} never waited on a lock within {timeout:g}s")
        await asyncio.sleep(0.01)


async def test_two_first_writes_at_once_both_succeed(postgres_sessions):
    maker = postgres_sessions
    async with maker() as setup:
        user = User(
            id=uuid.uuid4(),
            email=f"{uuid.uuid4().hex}@example.com",
            hashed_password="x",
            is_active=True,
            is_verified=True,
        )
        setup.add(user)
        await setup.commit()

    second_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def second_write():
        async with maker() as session:
            second_pid.set_result(await session.scalar(text("SELECT pg_backend_pid()")))
            quota = await quota_service.set_quota(session, user.id, {"max_bank_connections": 2})
            await session.commit()
            return quota.max_bank_connections

    async with maker() as first, maker() as watcher:
        await quota_service.set_quota(first, user.id, {"max_bank_connections": 1})
        second = asyncio.create_task(second_write())
        await _wait_until_blocked(watcher, await asyncio.wait_for(second_pid, 15))
        await first.commit()
        assert await asyncio.wait_for(second, 15) == 2

    async with maker() as check:
        quota = await quota_service.get_quota(check, user.id)
        assert quota is not None
        assert quota.max_bank_connections == 2
        assert quota.max_personal_workspaces is None
