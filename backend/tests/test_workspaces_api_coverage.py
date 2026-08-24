"""Coverage-focused API tests for app/api/workspaces.py.

Exercises list/create/get-current/update, member invite/list/role-change/
remove, stats, and archive — plus the error branches (404, 403, 400).
"""
import uuid

import pytest
from httpx import AsyncClient

from app.core.auth import get_jwt_strategy


# ---------------------------------------------------------------------------
# list / create / current
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_my_workspaces(client: AsyncClient, auth_headers, test_user):
    resp = await client.get("/api/workspaces", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    # The auto-created Personal workspace reports the owner role.
    assert any(w["role"] == "owner" for w in body)


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(client: AsyncClient):
    resp = await client.get("/api/workspaces")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_workspace_with_self_membership(client: AsyncClient, auth_headers, test_user):
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={
            "name": "Side Business",
            "kind": "business",
            "default_currency": "EUR",
            "self_membership": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Side Business"
    assert body["default_currency"] == "EUR"
    assert body["role"] == "owner"

    # It now shows up in the listing.
    resp = await client.get("/api/workspaces", headers=auth_headers)
    assert any(w["id"] == body["id"] for w in resp.json())


@pytest.mark.asyncio
async def test_create_workspace_manager_only(client: AsyncClient, auth_headers, test_user):
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Client Books", "self_membership": False},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "manager"
    # Manager-only workspaces still appear in the listing via managed union.
    resp = await client.get("/api/workspaces", headers=auth_headers)
    assert any(w["id"] == body["id"] and w["role"] == "manager" for w in resp.json())


@pytest.mark.asyncio
async def test_create_workspace_validation_422(client: AsyncClient, auth_headers, test_user):
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "", "default_currency": "TOOLONG"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_current_workspace(client: AsyncClient, auth_headers, test_user):
    resp = await client.get("/api/workspaces/current", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "personal"
    assert body["role"] in {"owner", "manager", "editor", "viewer"}


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_workspace(client: AsyncClient, auth_headers, test_workspace):
    resp = await client.patch(
        f"/api/workspaces/{test_workspace.id}",
        headers=auth_headers,
        json={"name": "Renamed", "color": "#123456"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["color"] == "#123456"
    assert body["role"] == "owner"


@pytest.mark.asyncio
async def test_update_workspace_not_member_404(client: AsyncClient, auth_headers, test_user):
    # A random workspace id the user has no membership/management on.
    resp = await client.patch(
        f"/api/workspaces/{uuid.uuid4()}",
        headers=auth_headers,
        json={"name": "Nope"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# members
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_members(client: AsyncClient, auth_headers, test_workspace, test_user):
    resp = await client.get(
        f"/api/workspaces/{test_workspace.id}/members", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert any(m["email"] == "test@example.com" and m["role"] == "owner" for m in body)


@pytest.mark.asyncio
async def test_invite_new_user_member(client: AsyncClient, auth_headers, test_workspace):
    resp = await client.post(
        f"/api/workspaces/{test_workspace.id}/members",
        headers=auth_headers,
        json={"email": "newcoll@example.com", "role": "editor", "password": "supersecret123"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "newcoll@example.com"
    assert body["role"] == "editor"


@pytest.mark.asyncio
async def test_invite_cannot_create_password_user_when_local_auth_disabled(
    client: AsyncClient,
    test_user,
    test_workspace,
    oidc_only_settings,
):
    token = await get_jwt_strategy().write_token(test_user)
    resp = await client.post(
        f"/api/workspaces/{test_workspace.id}/members",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": "oidc-only@example.com", "role": "editor", "password": "supersecret123"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "LOCAL_AUTH_DISABLED"


@pytest.mark.asyncio
async def test_invite_unknown_user_without_password_points_at_oidc_when_local_auth_disabled(
    client: AsyncClient,
    test_user,
    test_workspace,
    oidc_only_settings,
):
    token = await get_jwt_strategy().write_token(test_user)
    resp = await client.post(
        f"/api/workspaces/{test_workspace.id}/members",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": "oidc-only@example.com", "role": "editor"},
    )

    assert resp.status_code == 400
    # Asking for a password would be dead-end advice in OIDC-only mode.
    assert "password" not in resp.json()["detail"].lower()
    assert "identity provider" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_invite_unknown_user_without_password_400(client: AsyncClient, auth_headers, test_workspace):
    resp = await client.post(
        f"/api/workspaces/{test_workspace.id}/members",
        headers=auth_headers,
        json={"email": "ghost@example.com", "role": "editor"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invite_member_into_unknown_workspace_404(client: AsyncClient, auth_headers, test_user):
    resp = await client.post(
        f"/api/workspaces/{uuid.uuid4()}/members",
        headers=auth_headers,
        json={"email": "x@example.com", "role": "editor", "password": "supersecret123"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_change_member_role_and_remove(client: AsyncClient, auth_headers, test_workspace):
    # Invite a brand-new member.
    invite = await client.post(
        f"/api/workspaces/{test_workspace.id}/members",
        headers=auth_headers,
        json={"email": "rolechange@example.com", "role": "editor", "password": "supersecret123"},
    )
    assert invite.status_code == 201, invite.text
    member_user_id = invite.json()["user_id"]

    # Promote them to viewer.
    resp = await client.patch(
        f"/api/workspaces/{test_workspace.id}/members/{member_user_id}",
        headers=auth_headers,
        json={"role": "viewer"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "viewer"

    # Remove them.
    resp = await client.delete(
        f"/api/workspaces/{test_workspace.id}/members/{member_user_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_change_role_invalid_role_400(client: AsyncClient, auth_headers, test_workspace):
    invite = await client.post(
        f"/api/workspaces/{test_workspace.id}/members",
        headers=auth_headers,
        json={"email": "badrole@example.com", "role": "editor", "password": "supersecret123"},
    )
    member_user_id = invite.json()["user_id"]
    resp = await client.patch(
        f"/api/workspaces/{test_workspace.id}/members/{member_user_id}",
        headers=auth_headers,
        json={"role": "supreme_leader"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_demote_sole_owner_400(client: AsyncClient, auth_headers, test_workspace, test_user):
    resp = await client.patch(
        f"/api/workspaces/{test_workspace.id}/members/{test_user.id}",
        headers=auth_headers,
        json={"role": "editor"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_remove_sole_owner_400(client: AsyncClient, auth_headers, test_workspace, test_user):
    resp = await client.delete(
        f"/api/workspaces/{test_workspace.id}/members/{test_user.id}",
        headers=auth_headers,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# stats / archive
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_workspace_stats(client: AsyncClient, auth_headers, test_workspace):
    resp = await client.get(
        f"/api/workspaces/{test_workspace.id}/stats", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["members"] >= 1
    assert "accounts" in body
    assert "transactions" in body


@pytest.mark.asyncio
async def test_archive_last_workspace_400(client: AsyncClient, auth_headers, test_workspace):
    # The personal workspace is the user's only one -> can't archive it.
    resp = await client.post(
        f"/api/workspaces/{test_workspace.id}/archive", headers=auth_headers
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_archive_workspace_success(client: AsyncClient, auth_headers, test_workspace):
    # Create a second member-owned workspace so archiving the first is allowed.
    second = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Second", "self_membership": True},
    )
    assert second.status_code == 201, second.text

    resp = await client.post(
        f"/api/workspaces/{test_workspace.id}/archive", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_archived"] is True
