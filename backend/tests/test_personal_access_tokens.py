import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.personal_access_tokens import PAT_PREFIX, hash_token
from app.models.personal_access_token import PersonalAccessToken
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

pytestmark = pytest.mark.asyncio

TOKENS = "/api/personal-access-tokens"


async def _create(client: AsyncClient, headers: dict, **body) -> dict:
    response = await client.post(TOKENS, json={"name": "CI", **body}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(token: str, **extra) -> dict:
    return {"Authorization": f"Bearer {token}", **extra}


async def _second_workspace(session: AsyncSession, user: User) -> Workspace:
    ws = Workspace(name="Second", kind="personal", created_by_user_id=user.id)
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    await session.commit()
    return ws


async def test_create_returns_plaintext_once_and_stores_hash(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_workspace: Workspace
):
    created = await _create(client, auth_headers)
    assert created["token"].startswith(PAT_PREFIX)
    assert created["token"].startswith(created["token_prefix"])
    assert created["workspace_id"] == str(test_workspace.id)
    assert created["read_only"] is False
    assert created["expires_at"] is None

    row = (await session.execute(select(PersonalAccessToken))).scalar_one()
    assert row.token_hash == hash_token(created["token"])
    assert created["token"] not in (row.token_hash, row.token_prefix)

    listed = (await client.get(TOKENS, headers=auth_headers)).json()
    assert [t["id"] for t in listed] == [created["id"]]
    assert "token" not in listed[0]
    assert listed[0]["workspace_name"] == test_workspace.name


async def test_create_binds_to_active_workspace(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    other = await _second_workspace(session, test_user)
    created = await _create(client, {**auth_headers, "X-Workspace-Id": str(other.id)})
    assert created["workspace_id"] == str(other.id)


async def test_expiry_is_set_from_days(client: AsyncClient, auth_headers: dict):
    created = await _create(client, auth_headers, expires_in_days=30)
    expires = datetime.fromisoformat(created["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    delta = expires - datetime.now(timezone.utc)
    assert timedelta(days=29) < delta <= timedelta(days=30)


async def test_pat_reads_and_writes_workspace_routes(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    token = (await _create(client, auth_headers))["token"]

    assert (await client.get("/api/categories", headers=_bearer(token))).status_code == 200
    response = await client.post("/api/categories", json={"name": "Via PAT"}, headers=_bearer(token))
    assert response.status_code == 201, response.text

    row = (await session.execute(select(PersonalAccessToken))).scalar_one()
    await session.refresh(row)
    assert row.last_used_at is not None


async def test_pat_is_rejected_on_user_only_routes(client: AsyncClient, auth_headers: dict):
    token = (await _create(client, auth_headers))["token"]
    headers = _bearer(token)

    assert (await client.get("/api/users/me", headers=headers)).status_code == 401
    assert (await client.get("/api/workspaces", headers=headers)).status_code == 401
    assert (await client.get(TOKENS, headers=headers)).status_code == 401
    assert (await client.post(TOKENS, json={"name": "x"}, headers=headers)).status_code == 401


async def test_pat_cannot_mint_mcp_token(client: AsyncClient, auth_headers: dict):
    token = (await _create(client, auth_headers))["token"]

    response = await client.post("/api/agents/mcp-tokens", headers=_bearer(token))
    assert response.status_code == 401


async def test_workspace_header_must_match_token(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    other = await _second_workspace(session, test_user)
    token = (await _create(client, auth_headers))["token"]

    same = _bearer(token, **{"X-Workspace-Id": str(test_workspace.id).upper()})
    assert (await client.get("/api/categories", headers=same)).status_code == 200

    mismatch = _bearer(token, **{"X-Workspace-Id": str(other.id)})
    assert (await client.get("/api/categories", headers=mismatch)).status_code == 403


async def test_read_only_token_cannot_write(client: AsyncClient, auth_headers: dict):
    token = (await _create(client, auth_headers, read_only=True))["token"]

    assert (await client.get("/api/categories", headers=_bearer(token))).status_code == 200
    response = await client.post("/api/categories", json={"name": "Nope"}, headers=_bearer(token))
    assert response.status_code == 403


async def test_token_follows_live_role(
    client: AsyncClient,
    auth_headers: dict,
    session: AsyncSession,
    test_user: User,
    test_workspace: Workspace,
):
    token = (await _create(client, auth_headers))["token"]
    member = (
        await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == test_workspace.id,
                WorkspaceMember.user_id == test_user.id,
            )
        )
    ).scalar_one()

    member.role = "viewer"
    await session.commit()
    response = await client.post("/api/categories", json={"name": "Nope"}, headers=_bearer(token))
    assert response.status_code == 403

    await session.delete(member)
    await session.commit()
    assert (await client.get("/api/categories", headers=_bearer(token))).status_code == 404


async def test_expired_token_is_rejected(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    token = (await _create(client, auth_headers, expires_in_days=1))["token"]
    row = (await session.execute(select(PersonalAccessToken))).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()

    assert (await client.get("/api/categories", headers=_bearer(token))).status_code == 401


async def test_revoked_token_is_rejected(client: AsyncClient, auth_headers: dict):
    created = await _create(client, auth_headers)
    response = await client.delete(f"{TOKENS}/{created['id']}", headers=auth_headers)
    assert response.status_code == 204

    assert (await client.get("/api/categories", headers=_bearer(created["token"]))).status_code == 401
    assert (await client.get(TOKENS, headers=auth_headers)).json() == []


async def test_inactive_user_token_is_rejected(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    token = (await _create(client, auth_headers))["token"]
    test_user.is_active = False
    await session.commit()

    assert (await client.get("/api/categories", headers=_bearer(token))).status_code == 401


async def test_unknown_token_is_rejected(client: AsyncClient, test_user: User):
    headers = _bearer(f"{PAT_PREFIX}not-a-real-token")
    assert (await client.get("/api/categories", headers=headers)).status_code == 401


async def test_cannot_revoke_another_users_token(
    client: AsyncClient, auth_headers: dict, admin_auth_headers: dict
):
    created = await _create(client, auth_headers)
    response = await client.delete(f"{TOKENS}/{created['id']}", headers=admin_auth_headers)
    assert response.status_code == 404
    assert (await client.delete(f"{TOKENS}/{uuid.uuid4()}", headers=auth_headers)).status_code == 404
