"""Actual route/row-lock policy replacement preconditions on both native schemas."""
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api.workspaces import update_workspace
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.workspace import WorkspaceUpdate
from tests.test_review_concurrency_postgres import schema_sessions as schema_sessions
from tests.test_review_writer_races_postgres import seed, wait_for_real_block

pytestmark = pytest.mark.asyncio
DEFAULT = {'ingest': 'needs_review', 'manual': 'reviewed'}
CHANGED = {'ingest': 'reviewed', 'manual': 'reviewed'}


async def setup(factory):
    ids, _ = await seed(factory, 'policy')
    async with factory() as session:
        session.add(WorkspaceMember(workspace_id=ids['workspace'], user_id=ids['owner'], role='owner'))
        await session.commit()
    return ids


@pytest.mark.parametrize('expectation', ['missing', None, {}, {'ingest': 'needs_review'}, CHANGED])
async def test_missing_null_incomplete_stale_expectations_write_nothing(schema_sessions, expectation):
    ids = await setup(schema_sessions)
    async with schema_sessions() as session:
        user = await session.get(User, ids['owner'])
        before_preferences = dict(user.preferences or {})
        payload = {'review_policy': CHANGED, 'name': 'Must not persist', 'default_currency': 'EUR'}
        if expectation != 'missing':
            payload['expected_review_policy'] = expectation
        with pytest.raises(HTTPException) as caught:
            await update_workspace(ids['workspace'], WorkspaceUpdate.model_validate(payload), session, user)
        assert caught.value.status_code == 409
        assert not session.in_transaction()
    async with schema_sessions() as verify:
        ws = await verify.get(Workspace, ids['workspace'])
        user = await verify.get(User, ids['owner'])
        assert ws.review_policy is None and ws.name == 'Synthetic writer regression'
        assert ws.default_currency != 'EUR' and (user.preferences or {}) == before_preferences


async def test_null_reset_defaults_replacement_and_old_clients(schema_sessions):
    ids = await setup(schema_sessions)
    async with schema_sessions() as session:
        user = await session.get(User, ids['owner'])
        changed = await update_workspace(ids['workspace'], WorkspaceUpdate(
            review_policy={'ingest': 'reviewed'}, expected_review_policy=DEFAULT), session, user)
        assert changed.review_policy == {'ingest': 'reviewed'}  # replacement, not merge
        reset = await update_workspace(ids['workspace'], WorkspaceUpdate(
            review_policy=None, expected_review_policy=CHANGED), session, user)
        assert reset.review_policy is None
        legacy = await update_workspace(ids['workspace'], WorkspaceUpdate(name='Old client'), session, user)
        assert legacy.name == 'Old client' and legacy.review_policy is None
        assert not hasattr(await session.get(Workspace, ids['workspace']), 'expected_review_policy')


async def test_server_change_between_snapshot_and_patch_blocks_and_preserves_bundle(schema_sessions, monkeypatch):
    ids = await setup(schema_sessions)
    async with schema_sessions() as winner, schema_sessions() as loser, schema_sessions() as observer:
        held_workspace = await loser.get(Workspace, ids['workspace'])
        a_user = await winner.get(User, ids['owner'])
        b_user = await loser.get(User, ids['owner'])
        a_pid = await winner.scalar(text('SELECT pg_backend_pid()'))
        b_pid = await loser.scalar(text('SELECT pg_backend_pid()'))
        ready, release = asyncio.Event(), asyncio.Event()
        real_commit = winner.commit

        async def hold():
            await winner.flush()
            ready.set()
            await release.wait()
            await real_commit()

        monkeypatch.setattr(winner, 'commit', hold)
        a = asyncio.create_task(update_workspace(ids['workspace'], WorkspaceUpdate(
            review_policy=CHANGED, expected_review_policy=DEFAULT), winner, a_user))
        b = None
        try:
            await asyncio.wait_for(ready.wait(), 8)
            b = asyncio.create_task(update_workspace(ids['workspace'], WorkspaceUpdate(
                review_policy={'ingest': 'needs_review', 'manual': 'needs_review'},
                expected_review_policy=DEFAULT, name='Forbidden stale name', default_currency='EUR'), loser, b_user))
            await wait_for_real_block(observer, b, b_pid, a_pid)
            release.set()
            await asyncio.wait_for(a, 8)
            with pytest.raises(HTTPException) as caught:
                await asyncio.wait_for(b, 8)
            assert caught.value.status_code == 409
            assert not loser.in_transaction()
            assert held_workspace is not None  # strong stale identity-map reference
        finally:
            release.set()
            for task in (a, b):
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    async with schema_sessions() as verify:
        ws = await verify.get(Workspace, ids['workspace'])
        assert ws.review_policy == CHANGED and ws.name == 'Synthetic writer regression'
        assert ws.default_currency != 'EUR'
