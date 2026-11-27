"""Per-account limits on a managed instance (ADR 0049).

Four neutral counters, checked only with MANAGED_INSTANCE on, counted from
what the account owns: the workspaces it created and what those hold.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.access_token import AccessToken
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.plugin_connection import PluginConnection
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.plugins import connections
from app.providers.base import ConnectionData
from app.services import quota_service, workspace_service
from app.services.connection_service import handle_oauth_callback
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


async def _limit(session: AsyncSession, user_id: uuid.UUID, **values) -> None:
    await quota_service.set_quota(session, user_id, values)
    await session.commit()


async def _user(session: AsyncSession, email: str) -> User:
    user = User(id=uuid.uuid4(), email=email, hashed_password="x", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    return user


async def _workspace(session: AsyncSession, creator: User, kind: str = "personal") -> Workspace:
    workspace = Workspace(id=uuid.uuid4(), name="W", kind=kind, created_by_user_id=creator.id)
    session.add(workspace)
    await session.flush()
    return workspace


def _connection(workspace: Workspace, user_id: uuid.UUID) -> BankConnection:
    return BankConnection(
        id=uuid.uuid4(), workspace_id=workspace.id, user_id=user_id, provider="pluggy",
        external_id=uuid.uuid4().hex, institution_name="Bank", credentials={}, status="active",
    )


def _refused(error: HTTPException, counter: str, current: int, maximum: int) -> None:
    detail = error.detail
    assert error.status_code == 403 and isinstance(detail, dict)
    assert detail["code"] == "quota_exceeded"
    assert (detail["counter"], detail["current"], detail["maximum"]) == (counter, current, maximum)


# --- The switch -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_enforces_nothing(client: AsyncClient, session, test_user, auth_headers):
    await _limit(session, test_user.id, max_personal_workspaces=0, max_business_workspaces=0)
    resp = await client.post("/api/workspaces", json={"name": "Second"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_no_row_is_unlimited(managed, client: AsyncClient, test_user, auth_headers):
    resp = await client.post("/api/workspaces", json={"name": "Second"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text


# --- Workspaces ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_workspace_kind_has_its_own_number(managed, client: AsyncClient, session, test_user, auth_headers):
    await _limit(session, test_user.id, max_personal_workspaces=1, max_business_workspaces=1)
    resp = await client.post("/api/workspaces", json={"name": "Another"}, headers=auth_headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] | {"message": None} == {
        "code": "quota_exceeded", "counter": "max_personal_workspaces", "current": 1, "maximum": 1, "message": None,
    }
    resp = await client.post("/api/workspaces", json={"name": "Shop", "kind": "business"}, headers=auth_headers)
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_a_workspace_someone_invited_you_into_never_counts(managed, session: AsyncSession, test_user):
    other = await _user(session, "owner@example.com")
    theirs = await _workspace(session, other)
    session.add(WorkspaceMember(workspace_id=theirs.id, user_id=test_user.id, role="owner"))
    await session.flush()
    assert await quota_service.count_workspaces(session, test_user.id, "personal") == 1
    await _limit(session, test_user.id, max_personal_workspaces=2)
    await workspace_service.create_workspace(session, name="Mine", creator=test_user, seed_defaults=False)


@pytest.mark.asyncio
async def test_an_archived_workspace_gives_its_place_back(managed, session: AsyncSession, test_user, test_workspace):
    await _limit(session, test_user.id, max_personal_workspaces=1)
    with pytest.raises(HTTPException):
        await workspace_service.create_workspace(session, name="Again", creator=test_user, seed_defaults=False)
    test_workspace.is_archived = True
    await session.flush()
    await workspace_service.create_workspace(session, name="Again", creator=test_user, seed_defaults=False)


# --- Members -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_members_fill_the_creators_number(managed, session: AsyncSession, test_user, test_workspace):
    """The creator's own membership is not a seat; each invited person is."""
    await _limit(session, test_user.id, max_members_per_workspace=1)
    first, second = await _user(session, "a@example.com"), await _user(session, "b@example.com")
    await workspace_service.add_member(session, test_workspace.id, first.id)
    with pytest.raises(HTTPException) as refused:
        await workspace_service.add_member(session, test_workspace.id, second.id)
    _refused(refused.value, "max_members_per_workspace", 1, 1)


@pytest.mark.asyncio
async def test_the_member_route_answers_the_quota(managed, client: AsyncClient, session, test_user, test_workspace, auth_headers):
    await _limit(session, test_user.id, max_members_per_workspace=0)
    await _user(session, "friend@example.com")
    await session.commit()
    resp = await client.post(
        f"/api/workspaces/{test_workspace.id}/members",
        json={"email": "friend@example.com", "role": "editor"},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "quota_exceeded"


async def _user_count(session: AsyncSession) -> int:
    return len((await session.execute(select(User.id))).all())


@pytest.mark.asyncio
async def test_a_refused_invite_creates_no_account(managed, client: AsyncClient, session, test_user, test_workspace, auth_headers):
    """Over the member limit, inviting a new address leaves no user behind.

    Creating the account commits on its own, so a refusal checked only when
    the member is added used to leave a user with no workspace who could sign
    in with the password the owner typed.
    """
    await _limit(session, test_user.id, max_members_per_workspace=0)
    before = await _user_count(session)
    resp = await client.post(
        f"/api/workspaces/{test_workspace.id}/members",
        json={"email": "new-person@example.com", "role": "editor", "password": "a-long-password-1"},
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "quota_exceeded"
    session.expire_all()
    assert await _user_count(session) == before
    assert await session.scalar(select(User).where(User.email == "new-person@example.com")) is None


@pytest.mark.asyncio
async def test_an_account_created_by_an_invite_that_loses_the_last_seat_is_removed(
    managed, client: AsyncClient, session, test_user, test_workspace, auth_headers
):
    """The seat can go between the early check and the insert (a parallel
    invite); the account made for this invite goes with the refusal."""
    await _limit(session, test_user.id, max_members_per_workspace=1)
    before = await _user_count(session)
    real = quota_service.check_new_member
    calls = 0

    async def seat_taken_after_the_first_check(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise quota_service._refuse("max_members_per_workspace", 1, 1)
        await real(*args, **kwargs)

    with patch.object(quota_service, "check_new_member", seat_taken_after_the_first_check):
        resp = await client.post(
            f"/api/workspaces/{test_workspace.id}/members",
            json={"email": "raced@example.com", "role": "editor", "password": "a-long-password-1"},
            headers=auth_headers,
        )
    assert calls == 2
    assert resp.status_code == 403, resp.text
    session.expire_all()
    assert await _user_count(session) == before
    assert await session.scalar(select(User).where(User.email == "raced@example.com")) is None


@pytest.mark.asyncio
async def test_a_refused_invite_keeps_an_account_another_workspace_already_added(
    managed, client: AsyncClient, session, test_user, test_workspace, auth_headers
):
    """A parallel invite elsewhere can add the new account and commit before
    this one is refused; deleting it then would cascade that membership away."""
    await _limit(session, test_user.id, max_members_per_workspace=1)
    elsewhere_owner = await _user(session, "elsewhere-owner@example.com")
    elsewhere = await _workspace(session, elsewhere_owner, "business")
    elsewhere_id = elsewhere.id
    await session.commit()
    real = quota_service.check_new_member
    calls = 0

    async def added_elsewhere_then_seat_taken(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await real(*args, **kwargs)
        created = await session.scalar(select(User).where(User.email == "shared@example.com"))
        session.add(WorkspaceMember(workspace_id=elsewhere_id, user_id=created.id, role="editor"))
        await session.commit()
        raise quota_service._refuse("max_members_per_workspace", 1, 1)

    # No Personal workspace, so the only membership is the one made elsewhere.
    with patch.object(quota_service, "check_new_member", added_elsewhere_then_seat_taken), \
            patch.object(workspace_service, "create_personal_workspace_for_user", AsyncMock()):
        resp = await client.post(
            f"/api/workspaces/{test_workspace.id}/members",
            json={"email": "shared@example.com", "role": "editor", "password": "a-long-password-1"},
            headers=auth_headers,
        )
    assert calls == 2
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "quota_exceeded"
    session.expire_all()
    kept = await session.scalar(select(User).where(User.email == "shared@example.com"))
    assert kept is not None
    memberships = (await session.execute(
        select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == kept.id)
    )).scalars().all()
    assert memberships == [elsewhere_id]


@pytest.mark.asyncio
async def test_the_refusal_hands_the_new_account_to_the_service(
    managed, client: AsyncClient, session, test_user, test_workspace, auth_headers
):
    """The route leaves the decision to keep or delete the account to the service."""
    await _limit(session, test_user.id, max_members_per_workspace=1)
    real = quota_service.check_new_member
    calls = 0

    async def seat_taken_after_the_first_check(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise quota_service._refuse("max_members_per_workspace", 1, 1)
        await real(*args, **kwargs)

    drop = AsyncMock(wraps=workspace_service.drop_unattached_account)
    with patch.object(quota_service, "check_new_member", seat_taken_after_the_first_check), \
            patch.object(workspace_service, "drop_unattached_account", drop):
        resp = await client.post(
            f"/api/workspaces/{test_workspace.id}/members",
            json={"email": "handed@example.com", "role": "editor", "password": "a-long-password-1"},
            headers=auth_headers,
        )
    assert resp.status_code == 403, resp.text
    drop.assert_awaited_once()
    session.expire_all()
    assert drop.await_args is not None
    assert drop.await_args.args[1] is not None
    assert await session.scalar(select(User).where(User.email == "handed@example.com")) is None


@pytest.mark.asyncio
async def test_an_existing_account_refused_an_invite_is_never_handed_to_the_service(
    managed, client: AsyncClient, session, test_user, test_workspace, auth_headers
):
    await _limit(session, test_user.id, max_members_per_workspace=0)
    await _user(session, "existing@example.com")
    await session.commit()
    drop = AsyncMock()
    with patch.object(workspace_service, "drop_unattached_account", drop):
        resp = await client.post(
            f"/api/workspaces/{test_workspace.id}/members",
            json={"email": "existing@example.com", "role": "editor"},
            headers=auth_headers,
        )
    assert resp.status_code == 403, resp.text
    drop.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_service_deletes_an_account_with_no_workspace(session: AsyncSession):
    lonely = await _user(session, "lonely@example.com")
    lonely_id = lonely.id
    assert await workspace_service.drop_unattached_account(session, lonely_id) is True
    session.expire_all()
    assert await session.get(User, lonely_id) is None


@pytest.mark.asyncio
async def test_the_service_keeps_an_account_that_belongs_to_a_workspace(session: AsyncSession):
    owner = await _user(session, "keeper-owner@example.com")
    workspace = await _workspace(session, owner, "business")
    member = await _user(session, "kept@example.com")
    member_id = member.id
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=member_id, role="editor"))
    await session.flush()
    assert await workspace_service.drop_unattached_account(session, member_id) is False
    session.expire_all()
    assert await session.get(User, member_id) is not None


@pytest.mark.asyncio
async def test_an_invite_with_room_still_creates_the_account(managed, client: AsyncClient, session, test_user, test_workspace, auth_headers):
    await _limit(session, test_user.id, max_members_per_workspace=1)
    resp = await client.post(
        f"/api/workspaces/{test_workspace.id}/members",
        json={"email": "fits@example.com", "role": "editor", "password": "a-long-password-1"},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), resp.text
    session.expire_all()
    assert await session.scalar(select(User).where(User.email == "fits@example.com")) is not None


# --- Bank connections ----------------------------------------------------------


@pytest.mark.asyncio
async def test_connections_count_across_every_workspace_created_and_only_those(session: AsyncSession, test_user, test_workspace):
    second = await _workspace(session, test_user, "business")
    other = await _user(session, "else@example.com")
    theirs = await _workspace(session, other)
    session.add_all([
        _connection(test_workspace, test_user.id),
        _connection(second, test_user.id),
        _connection(theirs, test_user.id),  # added by test_user, but in someone else's workspace
        Account(id=uuid.uuid4(), workspace_id=test_workspace.id, user_id=test_user.id, name="Cash", type="checking"),
    ])
    await session.flush()
    assert await quota_service.count_bank_connections(session, test_user.id) == 2
    assert await quota_service.count_bank_connections(session, other.id) == 1


def _oauth_provider(external_id: str) -> AsyncMock:
    provider = AsyncMock()
    provider.handle_oauth_callback = AsyncMock(return_value=ConnectionData(
        external_id=external_id, institution_name="Bank", credentials={"token": "x"}, accounts=[],
    ))
    provider.get_holdings = AsyncMock(return_value=[])
    return provider


@pytest.mark.asyncio
async def test_the_native_path_refuses_past_the_limit(managed, session: AsyncSession, test_user, test_workspace):
    second = await _workspace(session, test_user, "business")
    session.add(_connection(second, test_user.id))
    await session.flush()
    await _limit(session, test_user.id, max_bank_connections=1)
    with patch("app.services.connection_service.get_provider", return_value=_oauth_provider("new")), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as refused:
            await handle_oauth_callback(session, test_workspace.id, test_user.id, "code", "pluggy", sync_assets=False)
    _refused(refused.value, "max_bank_connections", 1, 1)


@pytest.mark.asyncio
async def test_the_native_path_counts_for_the_workspace_creator(managed, session: AsyncSession, test_user, test_workspace):
    """An editor connecting a bank in someone's workspace fills the creator's number, not theirs."""
    editor = await _user(session, "editor@example.com")
    await _limit(session, test_user.id, max_bank_connections=0)
    await _limit(session, editor.id, max_bank_connections=5)
    with patch("app.services.connection_service.get_provider", return_value=_oauth_provider("x")), \
         patch("app.services.connection_service.detect_transfer_pairs", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as refused:
            await handle_oauth_callback(session, test_workspace.id, editor.id, "code", "pluggy", sync_assets=False)
    _refused(refused.value, "max_bank_connections", 0, 0)


@pytest.mark.asyncio
async def test_the_oauth_route_answers_403_not_400(managed, client: AsyncClient, session, test_user, auth_headers):
    await _limit(session, test_user.id, max_bank_connections=0)
    with patch("app.services.connection_service.get_provider", return_value=_oauth_provider("y")):
        resp = await client.post(
            "/api/connections/oauth/callback", json={"code": "c", "provider": "pluggy"}, headers=auth_headers,
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["counter"] == "max_bank_connections"


@pytest.mark.asyncio
async def test_the_plugin_path_refuses_past_the_limit(managed, session: AsyncSession, ids, provider_catalog, plugin_runner):
    workspace_id, user_id = ids
    await _limit(session, user_id, max_bank_connections=0)
    with pytest.raises(HTTPException) as refused:
        await provider_plugins._provider_connection(session, ids)
    _refused(refused.value, "max_bank_connections", 0, 0)


@pytest.mark.asyncio
async def test_the_plugin_path_counts_when_room_is_left(managed, session: AsyncSession, ids, provider_catalog, plugin_runner):
    workspace_id, user_id = ids
    await _limit(session, user_id, max_bank_connections=1)
    await provider_plugins._provider_connection(session, ids)
    assert await quota_service.count_bank_connections(session, user_id) == 1


@pytest.mark.asyncio
async def test_a_connection_that_fails_takes_its_token_with_it(
    managed, session: AsyncSession, ids, provider_catalog, plugin_runner
):
    """The plugin's token is created in the connection's transaction.

    A token committed on its own would outlive a connection that never
    happens, and would release the quota lock taken before it.
    """
    workspace_id, user_id = ids
    await _limit(session, user_id, max_bank_connections=5)
    await provider_plugins._apply(session, workspace_id, user_id)
    with patch.object(connections, "_add_bank", side_effect=RuntimeError("the bank insert failed")):
        with pytest.raises(RuntimeError):
            await provider_plugins._connect(session, workspace_id, user_id)
    await session.rollback()
    tokens = await session.scalars(select(AccessToken).where(AccessToken.workspace_id == workspace_id))
    assert list(tokens) == []
    rows = await session.scalars(select(PluginConnection).where(PluginConnection.workspace_id == workspace_id))
    assert list(rows) == []


# --- Fit check -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_fit_reports_the_excess_per_counter(session: AsyncSession, test_user, test_workspace):
    """Two workspaces and three connections over the target, as a screen would show."""
    second, third = await _workspace(session, test_user), await _workspace(session, test_user)
    session.add_all([
        _connection(test_workspace, test_user.id), _connection(second, test_user.id), _connection(third, test_user.id),
    ])
    for email in ("m1@example.com", "m2@example.com", "m3@example.com"):
        member = await _user(session, email)
        session.add(WorkspaceMember(workspace_id=test_workspace.id, user_id=member.id, role="viewer"))
    await session.flush()

    result = await quota_service.check_fit(session, test_user.id, {
        "max_personal_workspaces": 1, "max_business_workspaces": None,
        "max_bank_connections": 0, "max_members_per_workspace": 1,
    })
    assert not result.allowed
    by = {f.counter: (f.current, f.target, f.excess) for f in result.counters}
    assert by == {
        "max_personal_workspaces": (3, 1, 2),
        "max_business_workspaces": (0, None, 0),
        "max_bank_connections": (3, 0, 3),
        "max_members_per_workspace": (3, 1, 2),
    }

    fits = await quota_service.check_fit(session, test_user.id, {
        "max_personal_workspaces": 3, "max_bank_connections": 3, "max_members_per_workspace": 3,
    })
    assert fits.allowed and all(f.excess == 0 for f in fits.counters)


# --- The operator route --------------------------------------------------------


@pytest.mark.asyncio
async def test_without_operator_token_the_route_does_not_exist(client: AsyncClient, test_user, admin_auth_headers):
    resp = await client.put(f"/api/operator/quotas/{test_user.id}", json={}, headers=admin_auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_an_instance_superuser_is_refused(operator, client: AsyncClient, test_user, admin_auth_headers):
    resp = await client.put(
        f"/api/operator/quotas/{test_user.id}", json={"max_bank_connections": 99}, headers=admin_auth_headers,
    )
    assert resp.status_code == 403
    resp = await client.get(f"/api/operator/quotas/{test_user.id}", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_the_operator_writes_reads_and_checks_a_fit(operator, client: AsyncClient, test_user):
    url = f"/api/operator/quotas/{test_user.id}"
    resp = await client.get(url, headers=operator)
    assert resp.status_code == 200 and resp.json()["max_bank_connections"] is None

    body = {"max_personal_workspaces": 1, "max_business_workspaces": 0, "max_bank_connections": 3, "max_members_per_workspace": 2}
    resp = await client.put(url, json=body, headers=operator)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"user_id": str(test_user.id), **body}
    assert (await client.get(url, headers=operator)).json() == resp.json()

    resp = await client.put(url, json={"max_bank_connections": -1}, headers=operator)
    assert resp.status_code == 422

    # The columns are 32-bit integers: past that the database refused with a 500.
    for counter in quota_service.COUNTERS:
        resp = await client.put(url, json={counter: 2**31}, headers=operator)
        assert resp.status_code == 422, (counter, resp.text)
        resp = await client.post(f"{url}/fit-check", json={counter: 1099511627776}, headers=operator)
        assert resp.status_code == 422, (counter, resp.text)
    resp = await client.put(url, json={"max_bank_connections": 2**31 - 1}, headers=operator)
    assert resp.status_code == 200, resp.text
    resp = await client.put(url, json=body, headers=operator)

    resp = await client.post(f"{url}/fit-check", json={"max_personal_workspaces": 0}, headers=operator)
    assert resp.status_code == 200
    assert resp.json()["allowed"] is False
    personal = next(c for c in resp.json()["counters"] if c["counter"] == "max_personal_workspaces")
    assert personal == {"counter": "max_personal_workspaces", "current": 1, "target": 0, "excess": 1}

    resp = await client.put(f"/api/operator/quotas/{uuid.uuid4()}", json={}, headers=operator)
    assert resp.status_code == 404
