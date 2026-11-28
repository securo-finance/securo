"""What an excess over the account's numbers does, and from when (ADR 0055).

An account above its numbers keeps everything. Until the operator sets a
date nothing changes; past it, bank sync stops while the account holds more
connections than its number, and starts again by itself once it fits.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.bank_connection import BankConnection
from app.models.notification import Notification
from app.models.user import User
from app.models.workspace import Workspace
from app.plugins import connections as plugin_connections
from app.services import notification_service, quota_service
from tests import test_provider_plugins as provider_plugins

catalog = provider_plugins.catalog
plugin_runner = provider_plugins.plugin_runner
states = provider_plugins.states
sync_locks = provider_plugins.sync_locks
ids = provider_plugins.ids
provider_catalog = provider_plugins.provider_catalog

TOKEN = "operator-secret-token"


@pytest.fixture
def managed(monkeypatch):
    monkeypatch.setattr(get_settings(), "managed_instance", True)


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.setattr(get_settings(), "operator_token", SecretStr(TOKEN))
    return {"Authorization": f"Bearer {TOKEN}"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _connections(session: AsyncSession, workspace: Workspace, user: User, count: int) -> list[BankConnection]:
    rows = [
        BankConnection(
            id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, provider="pluggy",
            external_id=uuid.uuid4().hex, institution_name="Bank", credentials={"item_id": "x"}, status="active",
        )
        for _ in range(count)
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def _above(session: AsyncSession, user: User, workspace: Workspace, *, date: datetime | None) -> list[BankConnection]:
    """Two connections against a number of one, with the operator's date."""
    rows = await _connections(session, workspace, user, 2)
    await quota_service.set_quota(session, user.id, {"max_bank_connections": 1})
    await quota_service.set_excess_date(session, user.id, date)
    await session.commit()
    return rows


async def _open(session: AsyncSession, user_id: uuid.UUID) -> list[Notification]:
    rows = await session.execute(
        select(Notification).where(
            Notification.user_id == user_id,
            Notification.kind == notification_service.QUOTA_EXCESS,
            Notification.resolved_at.is_(None),
        )
    )
    return list(rows.scalars())


async def test_without_a_date_an_excess_changes_nothing(managed, session, test_user, test_workspace):
    await _above(session, test_user, test_workspace, date=None)
    await quota_service.check_sync(session, test_workspace.id)
    standing = await quota_service.standing(session, test_user.id)
    assert standing is not None and not standing.fit.allowed
    assert not standing.in_effect and not standing.sync_paused


async def test_before_the_date_sync_goes_on(managed, session, test_user, test_workspace):
    await _above(session, test_user, test_workspace, date=_now() + timedelta(days=7))
    await quota_service.check_sync(session, test_workspace.id)


async def test_past_the_date_sync_pauses_and_nothing_is_removed(managed, session, test_user, test_workspace):
    rows = await _above(session, test_user, test_workspace, date=_now() - timedelta(minutes=1))
    with pytest.raises(quota_service.SyncPausedError) as caught:
        await quota_service.check_sync(session, test_workspace.id)
    assert caught.value.detail()["code"] == "quota_sync_paused"
    assert (caught.value.current, caught.value.maximum) == (2, 1)
    for row in rows:
        await session.refresh(row)
        assert row.status == "active"


async def test_fitting_again_resumes_sync(managed, session, test_user, test_workspace):
    rows = await _above(session, test_user, test_workspace, date=_now() - timedelta(minutes=1))
    await session.delete(rows[0])
    await session.commit()
    await quota_service.check_sync(session, test_workspace.id)
    standing = await quota_service.standing(session, test_user.id)
    assert standing is not None and standing.fit.allowed and not standing.sync_paused


async def test_off_the_managed_instance_the_date_does_nothing(session, test_user, test_workspace):
    await _above(session, test_user, test_workspace, date=_now() - timedelta(minutes=1))
    await quota_service.check_sync(session, test_workspace.id)


async def test_a_plugin_sync_pauses_before_the_plugin_runs(managed, session, ids, provider_catalog, plugin_runner):
    workspace_id, user_id = ids
    connection = await provider_plugins._provider_connection(session, ids)
    synced_at = connection.last_sync_at
    workspace = await session.get(Workspace, workspace_id)
    user = await session.get(User, user_id)
    assert workspace is not None and user is not None
    await _above(session, user, workspace, date=_now() - timedelta(minutes=1))
    with pytest.raises(quota_service.SyncPausedError):
        await plugin_connections.sync(session, connection)
    await session.refresh(connection)
    assert connection.last_sync_at == synced_at and connection.status != "needs_auth"


async def test_a_plugin_sync_the_date_reaches_mid_run_is_a_pause_not_a_failure(
    managed, session, ids, provider_catalog, plugin_runner, monkeypatch
):
    connection = await provider_plugins._provider_connection(session, ids)
    before = (connection.status, connection.failures, connection.last_sync_at, dict(connection.state or {}))
    bank = await provider_plugins._bank(session, connection)
    bank_status = bank.status
    calls = 0

    async def date_comes_while_the_plugin_runs(session, workspace_id, now=None):
        nonlocal calls
        calls += 1
        if calls > 1:  # the check inside the write, after the plugin answered
            raise quota_service.SyncPausedError(2, 1)

    monkeypatch.setattr(quota_service, "check_sync", date_comes_while_the_plugin_runs)
    with pytest.raises(quota_service.SyncPausedError):
        await plugin_connections.sync(session, connection)
    assert calls == 2
    await session.refresh(connection)
    assert (connection.status, connection.failures, connection.last_sync_at, dict(connection.state or {})) == before
    assert (await provider_plugins._bank(session, connection)).status == bank_status


async def test_a_moved_date_is_told_again_and_so_is_its_arrival(
    managed, session, test_user, test_workspace, monkeypatch
):
    user_id = test_user.id
    await _above(session, test_user, test_workspace, date=_now() + timedelta(days=3))
    await notification_service.quota_changed(session, user_id)
    [first] = await _open(session, user_id)
    first.read_at = _now()
    await session.commit()

    # Postponed: a notice of its own, unread.
    later = _now() + timedelta(days=10)
    await quota_service.set_excess_date(session, user_id, later)
    await notification_service.quota_changed(session, user_id)
    await session.commit()
    [moved] = await _open(session, user_id)
    assert moved.id != first.id and moved.read_at is None and moved.data["newest"] == "0"
    moved.read_at = _now()
    await session.commit()

    # Read again, unchanged: stays read.
    await notification_service.quota_changed(session, user_id)
    await session.commit()
    [same] = await _open(session, user_id)
    assert same.id == moved.id and same.read_at is not None

    # The postponed date comes: unread again.
    monkeypatch.setattr(quota_service, "in_effect", lambda quota, now=None: True)
    await notification_service.quota_changed(session, user_id)
    await session.commit()
    [arrived] = await _open(session, user_id)
    assert arrived.id == moved.id and arrived.read_at is None and arrived.data["newest"] == "1"


async def test_the_manual_sync_answers_403(managed, client: AsyncClient, session, test_user, test_workspace, auth_headers):
    rows = await _above(session, test_user, test_workspace, date=_now() - timedelta(minutes=1))
    resp = await client.post(f"/api/connections/{rows[0].id}/sync", headers=auth_headers)
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "quota_sync_paused" and detail["current"] == 2 and detail["maximum"] == 1


async def test_the_operator_route_needs_the_token(client: AsyncClient, test_user, admin_auth_headers):
    url = f"/api/operator/quotas/{test_user.id}/excess-date"
    resp = await client.put(url, json={"effective_at": None}, headers=admin_auth_headers)
    assert resp.status_code == 404


async def test_the_operator_sets_the_date_and_the_account_is_told(
    managed, operator, client: AsyncClient, session, test_user, test_workspace
):
    user_id, workspace_id = test_user.id, test_workspace.id
    await _connections(session, test_workspace, test_user, 2)
    await session.commit()
    url = f"/api/operator/quotas/{user_id}"
    assert (await client.put(url, json={"max_bank_connections": 1}, headers=operator)).status_code == 200
    assert await _open(session, user_id) == []  # no date yet: nothing to tell

    when = _now() + timedelta(days=14)
    resp = await client.put(f"{url}/excess-date", json={"effective_at": when.isoformat()}, headers=operator)
    assert resp.status_code == 200, resp.text
    assert resp.json()["max_bank_connections"] == 1
    assert datetime.fromisoformat(resp.json()["excess_effective_at"]) == when
    assert datetime.fromisoformat((await client.get(url, headers=operator)).json()["excess_effective_at"]) == when

    session.expire_all()
    [told] = await _open(session, user_id)
    assert told.workspace_id == workspace_id
    assert told.data["in_effect"] is False and told.data["newest"] == "0"
    assert told.data["counters"] == [{"counter": "max_bank_connections", "current": 2, "maximum": 1, "excess": 1}]

    # The date comes: told again, now with sync paused.
    resp = await client.put(f"{url}/excess-date", json={"effective_at": (_now() - timedelta(minutes=1)).isoformat()}, headers=operator)
    assert resp.status_code == 200
    session.expire_all()
    [told] = await _open(session, user_id)
    assert told.data["in_effect"] is True and told.data["sync_paused"] is True and told.data["newest"] == "1"

    # Cleared: the notification is over.
    resp = await client.put(f"{url}/excess-date", json={"effective_at": None}, headers=operator)
    assert resp.status_code == 200 and resp.json()["excess_effective_at"] is None
    session.expire_all()
    assert await _open(session, user_id) == []


async def test_the_scheduled_check_ends_it_once_the_account_fits(managed, session, test_user, test_workspace):
    user_id, workspace_id = test_user.id, test_workspace.id
    rows = await _above(session, test_user, test_workspace, date=_now() - timedelta(minutes=1))
    await notification_service.quota_changed(session, user_id)
    await session.commit()
    assert len(await _open(session, user_id)) == 1
    await session.delete(rows[0])
    await session.commit()
    await notification_service.evaluate_workspace(session, workspace_id)
    await session.commit()
    session.expire_all()
    assert await _open(session, user_id) == []


async def test_the_kind_exists_only_on_a_managed_instance(session, test_workspace, monkeypatch):
    assert notification_service.QUOTA_EXCESS not in notification_service.kinds_for(test_workspace, can_write=True)
    monkeypatch.setattr(get_settings(), "managed_instance", True)
    assert notification_service.QUOTA_EXCESS in notification_service.kinds_for(test_workspace, can_write=True)


async def test_my_standing_only_on_a_managed_instance(client: AsyncClient, auth_headers):
    resp = await client.get("/api/quotas/mine", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["enforced"] is False and body["above"] is False and body["effective_at"] is None


async def test_my_standing_shows_the_excess(managed, client: AsyncClient, session, test_user, test_workspace, auth_headers):
    resp = await client.get("/api/quotas/mine", headers=auth_headers)
    assert resp.status_code == 200 and resp.json()["above"] is False and resp.json()["counters"] == []

    await _above(session, test_user, test_workspace, date=_now() - timedelta(minutes=1))
    body = (await client.get("/api/quotas/mine", headers=auth_headers)).json()
    assert body["above"] is True and body["in_effect"] is True and body["sync_paused"] is True
    connections = next(c for c in body["counters"] if c["counter"] == "max_bank_connections")
    assert connections == {"counter": "max_bank_connections", "current": 2, "maximum": 1, "excess": 1}


async def test_a_date_on_an_account_held_by_the_defaults_keeps_the_defaults(
    managed, operator, client: AsyncClient, session, test_user, test_workspace, auth_headers
):
    """The date alone must not hand the account a row of its own with every number unbounded (ADR 0056)."""
    user_id, workspace_id = test_user.id, test_workspace.id
    await _connections(session, test_workspace, test_user, 2)
    await quota_service.set_defaults(session, {"max_bank_connections": 1})
    await session.commit()

    url = f"/api/operator/quotas/{user_id}"
    past = (_now() - timedelta(minutes=1)).isoformat()
    resp = await client.put(f"{url}/excess-date", json={"effective_at": past}, headers=operator)
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "default" and resp.json()["max_bank_connections"] == 1
    assert resp.json()["excess_effective_at"] is not None

    body = (await client.get("/api/quotas/mine", headers=auth_headers)).json()
    assert body["above"] is True and body["in_effect"] is True and body["sync_paused"] is True

    session.expire_all()
    with pytest.raises(quota_service.SyncPausedError):
        await quota_service.check_sync(session, workspace_id)

    # The defaults still move the account: lifting them ends the pause.
    await quota_service.set_defaults(session, {"max_bank_connections": 2})
    await session.commit()
    await quota_service.check_sync(session, workspace_id)
