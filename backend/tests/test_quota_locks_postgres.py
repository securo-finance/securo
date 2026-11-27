"""The quota gates hold an advisory lock for the account they count (ADR 0049).

The count and the insert it guards run in the caller's transaction, so under
READ COMMITTED two parallel requests from the same owner could both count below
the limit and both insert. The gate takes a transaction-scoped advisory lock
first, keyed on the owner (workspaces, connections) or on the workspace
(members). SQLite serialises writers itself, so only here can the lock be seen:
the transaction that passed a gate holds it until it ends, and a parallel one
cannot take the same key.
"""
import asyncio
import uuid
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.core.config import get_settings
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_plugin import WorkspacePlugin
from app.plugins import connections
from app.services import access_token_service, quota_service
from tests import test_provider_plugins as provider_plugins
from tests.test_code_plugins import PLUGIN

#: How `pg_advisory_xact_lock(hashtext(key))` stores its 8-byte key: split
#: across `classid` (high half) and `objid` (low half). Both queries below match
#: on it, so an advisory lock of another test running in parallel is never
#: mistaken for this one's.
_KEY_MATCH = """
      AND objsubid = 1
      AND classid = ((hashtext(:key)::bigint >> 32) & 4294967295)::oid
      AND objid = (hashtext(:key)::bigint & 4294967295)::oid
"""

#: How many advisory locks this very transaction holds for `key`.
_HELD = text(
    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()" + _KEY_MATCH
)

#: How many other sessions are queued for `key` right now (`granted = false`).
_QUEUED = text(
    "SELECT count(*) FROM pg_locks"
    " WHERE locktype = 'advisory' AND NOT granted AND pid <> pg_backend_pid()" + _KEY_MATCH
)

#: Try to take the same transaction-scoped lock from another connection.
_TRY_TAKE = text("SELECT pg_try_advisory_xact_lock(hashtext(:key))")

_HELD_ANY = text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()")


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", True)


@pytest.fixture
def unmanaged(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", False)


async def _user_with_quota(session, **limits) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    await quota_service.set_quota(session, user.id, limits)
    return user


async def _workspace(session, creator: User) -> Workspace:
    workspace = Workspace(id=uuid.uuid4(), name="W", kind="personal", created_by_user_id=creator.id)
    session.add(workspace)
    await session.flush()
    return workspace


async def _wait_until_queued(session, key: str, timeout: float = 15.0) -> None:
    """Wait until another session is blocked asking for `key`, or fail.

    What this test has to show is the lock serialising the two requests, not
    the order the tasks happened to run in: the first request stays inside its
    transaction until the second is really queued on `pg_advisory_xact_lock`
    (`granted = false` in `pg_locks`). A flag set before the second reaches the
    lock would let it count after the first commits and be refused anyway,
    which proves nothing about contention.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not await session.scalar(_QUEUED, {"key": key}):
        if loop.time() >= deadline:
            raise AssertionError(f"no other session queued on {key!r} within {timeout:g}s")
        await asyncio.sleep(0.01)


async def test_each_gate_holds_its_account_lock_until_the_transaction_ends(postgres_sessions, managed):
    """A gate that passes leaves the lock held; another transaction cannot take it."""
    async with postgres_sessions() as gate:
        user = await _user_with_quota(
            gate,
            max_personal_workspaces=5,
            max_bank_connections=5,
            max_members_per_workspace=5,
        )
        workspace = await _workspace(gate, user)
        await quota_service.check_new_workspace(gate, user.id, "personal")
        await quota_service.check_new_bank_connection(gate, workspace.id)
        await quota_service.check_new_member(gate, workspace.id, uuid.uuid4())

        keys = [
            f"quota:workspaces:{user.id}",
            f"quota:connections:{user.id}",
            f"quota:members:{workspace.id}",
        ]
        for key in keys:
            assert await gate.scalar(_HELD, {"key": key}) == 1, key

        async with postgres_sessions() as parallel:
            for key in keys:
                assert await parallel.scalar(_TRY_TAKE, {"key": key}) is False, key
            # Another owner's key is free: the gate serialises per account, not globally.
            assert await parallel.scalar(_TRY_TAKE, {"key": f"quota:workspaces:{uuid.uuid4()}"}) is True

    async with postgres_sessions() as later:
        for key in keys:
            assert await later.scalar(_TRY_TAKE, {"key": key}) is True, key


async def test_no_lock_when_the_limit_does_not_apply(postgres_sessions, unmanaged, monkeypatch):
    """Off, with no quota row, or without an owner there is nothing to serialise."""
    async with postgres_sessions() as session:
        user = await _user_with_quota(session)
        workspace = await _workspace(session, user)
        await quota_service.check_new_workspace(session, user.id, "personal")
        await quota_service.check_new_bank_connection(session, workspace.id)
        await quota_service.check_new_member(session, workspace.id, uuid.uuid4())
        assert await session.scalar(_HELD_ANY) == 0

        # Managed on, but the counters are unset: no limit, so no lock.
        monkeypatch.setattr(get_settings(), "managed_instance", True)
        await quota_service.check_new_workspace(session, user.id, "personal")
        await quota_service.check_new_bank_connection(session, workspace.id)
        assert await session.scalar(_HELD_ANY) == 0

        # A workspace nobody owns has no quota to hold.
        await quota_service.check_new_bank_connection(session, uuid.uuid4())
        await quota_service.check_new_member(session, workspace.id, user.id)
        assert await session.scalar(_HELD_ANY) == 0


async def _plugin_workspace(maker, **limits) -> tuple[uuid.UUID, uuid.UUID]:
    """A workspace with the provider plugin installed and a limit on connections."""
    async with maker() as session:
        user = User(
            id=uuid.uuid4(),
            email=f"{uuid.uuid4().hex}@example.com",
            hashed_password="x",
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        workspace = Workspace(id=uuid.uuid4(), name="W", kind="personal", created_by_user_id=user.id)
        session.add(workspace)
        await session.flush()
        session.add(
            WorkspacePlugin(
                workspace_id=workspace.id,
                plugin_id=PLUGIN,
                name={"en": "Ledger"},
                version="1.0.0",
                commit="c0ffee",
                manifest=provider_plugins._provider_manifest(),
            )
        )
        await quota_service.set_quota(session, user.id, limits)
        await session.commit()
        return workspace.id, user.id


async def test_a_plugin_connection_cannot_pass_the_limit_while_its_token_is_created(postgres_sessions, managed):
    """The lock taken before the count still covers the insert after the token.

    The plugin creates its API token between the count and the connection
    row. Committed on its own, that token releases the lock before this
    request inserts, and a parallel one counts first: both pass. The token is
    made inside the connection's transaction (`commit=False`), so the second
    waits and then counts the row.
    """
    maker = postgres_sessions
    workspace_id, user_id = await _plugin_workspace(maker, max_bank_connections=1)

    real_create_token = access_token_service.create_token
    at_token = asyncio.Event()
    lock_key = f"quota:connections:{user_id}"

    async def pausing_create_token(session, **kwargs):
        # Hold the first request right where the plugin's token is made until
        # the second one is queued on the quota lock: only then may this
        # transaction end and release it.
        created = await real_create_token(session, **kwargs)
        at_token.set()
        await _wait_until_queued(session, lock_key)
        return created

    async def connect(waits_for=None):
        if waits_for is not None:
            await waits_for.wait()
        async with maker() as session:
            try:
                return await connections.create(
                    session,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    can_write=True,
                    plugin_id=PLUGIN,
                    integration_key="keys",
                    name="Ledger",
                    values={"api_key": "sk_live_1"},
                    lang="en",
                )
            finally:
                await session.rollback()

    with patch.object(access_token_service, "create_token", pausing_create_token):
        first = asyncio.create_task(connect())
        second = asyncio.create_task(connect(at_token))
        results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 60)

    refused = [r for r in results if isinstance(r, HTTPException)]
    assert len(refused) == 1, results
    assert refused[0].status_code == 403 and isinstance(refused[0].detail, dict)
    assert refused[0].detail["counter"] == "max_bank_connections"
    async with maker() as check:
        assert await quota_service.count_bank_connections(check, user_id) == 1
