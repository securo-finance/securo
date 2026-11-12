"""Access tokens: scoped, workspace-bound credentials for the REST API."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_token_scopes import ALL_SCOPES, grants, required_scope
from app.models.access_token import AccessToken
from app.models.workspace import Workspace, WorkspaceMember
from app.services.access_token_service import hash_token


# --- Scope catalog ------------------------------------------------------------


def test_read_and_write_map_to_methods():
    assert required_scope("GET", "/api/transactions") == "transactions:read"
    assert required_scope("POST", "/api/transactions") == "transactions:write"
    assert required_scope("DELETE", "/api/transactions/abc/attachments/x") == "transactions:write"
    assert required_scope("GET", "/api/category-groups") == "categories:read"
    assert required_scope("GET", "/api/dashboard/summary") == "reports:read"


def test_unlisted_routes_are_closed_to_tokens():
    for path in ("/api/workspaces", "/api/access-tokens", "/api/users/me", "/api/admin/users", "/api/export"):
        assert required_scope("GET", path) is None
    # Reports only read, so there is no way to write through them.
    assert required_scope("POST", "/api/reports/net-worth") is None
    assert "reports:write" not in ALL_SCOPES


def test_prefix_match_is_on_path_segments():
    assert required_scope("GET", "/api/accountsfoo") is None


def test_reference_data_needs_no_scope():
    assert required_scope("GET", "/api/currencies") == ""
    assert required_scope("POST", "/api/currencies") is None


def test_write_includes_read():
    assert grants(["accounts:write"], "accounts:read")
    assert not grants(["accounts:read"], "accounts:write")
    assert not grants(["accounts:write"], "transactions:read")


# --- Helpers ------------------------------------------------------------------


async def _create(client: AsyncClient, headers: dict, scopes: list[str], **extra) -> dict:
    resp = await client.post(
        "/api/access-tokens", headers=headers, json={"name": "script", "scopes": scopes, **extra}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bearer(token: str, **extra: str) -> dict:
    return {"Authorization": f"Bearer {token}", **extra}


# --- Lifecycle ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_is_shown_once_and_only_its_hash_is_stored(
    client: AsyncClient, auth_headers: dict, session: AsyncSession
):
    created = await _create(client, auth_headers, ["accounts:read"])
    secret = created["token"]
    assert secret.startswith("sct_")
    assert secret.startswith(created["token_prefix"])

    row = (await session.execute(select(AccessToken))).scalar_one()
    assert row.token_hash == hash_token(secret)
    assert secret not in (row.token_prefix, row.token_hash)

    listed = (await client.get("/api/access-tokens", headers=auth_headers)).json()
    assert [t["id"] for t in listed] == [created["id"]]
    assert "token" not in listed[0]
    assert listed[0]["is_active"] is True


@pytest.mark.asyncio
async def test_unknown_scope_is_rejected(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        "/api/access-tokens", headers=auth_headers, json={"name": "x", "scopes": ["admin:write"]}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_scopes_list(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/access-tokens/scopes", headers=auth_headers)
    assert resp.json()["scopes"] == list(ALL_SCOPES)


# --- Using a token --------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_scope_reads_but_cannot_write(client: AsyncClient, auth_headers: dict, test_user):
    token = (await _create(client, auth_headers, ["categories:read"]))["token"]

    assert (await client.get("/api/categories", headers=_bearer(token))).status_code == 200
    resp = await client.post("/api/categories", headers=_bearer(token), json={"name": "Pets"})
    assert resp.status_code == 403
    # Outside its scopes entirely.
    assert (await client.get("/api/accounts", headers=_bearer(token))).status_code == 403


@pytest.mark.asyncio
async def test_write_scope_can_write(client: AsyncClient, auth_headers: dict, test_user):
    token = (await _create(client, auth_headers, ["categories:write"]))["token"]
    resp = await client.post("/api/categories", headers=_bearer(token), json={"name": "Pets"})
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_token_cannot_reach_account_or_token_management(client: AsyncClient, auth_headers: dict):
    token = (await _create(client, auth_headers, list(ALL_SCOPES)))["token"]
    for method, path in (
        ("GET", "/api/workspaces"),
        ("GET", "/api/access-tokens"),
        ("POST", "/api/access-tokens"),
        ("GET", "/api/users/me"),
        ("GET", "/api/admin/users"),
    ):
        resp = await client.request(method, path, headers=_bearer(token), json={"name": "x", "scopes": ["accounts:read"]})
        assert resp.status_code in (401, 403), (method, path, resp.status_code)


@pytest.mark.asyncio
async def test_reference_data_is_readable_with_any_token(client: AsyncClient, auth_headers: dict):
    token = (await _create(client, auth_headers, ["goals:read"]))["token"]
    assert (await client.get("/api/currencies", headers=_bearer(token))).status_code == 200


@pytest.mark.asyncio
async def test_token_is_pinned_to_its_workspace(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user
):
    token = (await _create(client, auth_headers, ["categories:read"]))["token"]

    other = Workspace(id=uuid.uuid4(), name="Other", kind="personal", default_currency="USD", locale="en")
    session.add(other)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other.id, user_id=test_user.id, role="owner"))
    await session.commit()

    resp = await client.get("/api/categories", headers=_bearer(token, **{"X-Workspace-Id": str(other.id)}))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_revoked_token_stops_working(client: AsyncClient, auth_headers: dict):
    created = await _create(client, auth_headers, ["categories:read"])
    resp = await client.delete(f"/api/access-tokens/{created['id']}", headers=auth_headers)
    assert resp.status_code == 204

    assert (await client.get("/api/categories", headers=_bearer(created["token"]))).status_code == 401
    listed = (await client.get("/api/access-tokens", headers=auth_headers)).json()
    assert listed[0]["is_active"] is False
    assert listed[0]["revoked_at"] is not None


@pytest.mark.asyncio
async def test_expired_token_stops_working(client: AsyncClient, auth_headers: dict, session: AsyncSession):
    created = await _create(client, auth_headers, ["categories:read"], expires_in_days=1)
    row = await session.get(AccessToken, uuid.UUID(created["id"]))
    assert row is not None
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()

    assert (await client.get("/api/categories", headers=_bearer(created["token"]))).status_code == 401


@pytest.mark.asyncio
async def test_token_without_expiry(client: AsyncClient, auth_headers: dict):
    created = await _create(client, auth_headers, ["categories:read"], expires_in_days=None)
    assert created["expires_at"] is None
    assert (await client.get("/api/categories", headers=_bearer(created["token"]))).status_code == 200


@pytest.mark.asyncio
async def test_use_records_last_used(client: AsyncClient, auth_headers: dict):
    created = await _create(client, auth_headers, ["categories:read"])
    assert created["last_used_at"] is None
    await client.get("/api/categories", headers=_bearer(created["token"]))
    listed = (await client.get("/api/access-tokens", headers=auth_headers)).json()
    assert listed[0]["last_used_at"] is not None


@pytest.mark.asyncio
async def test_garbage_bearer_is_unauthorized(client: AsyncClient):
    assert (await client.get("/api/categories", headers=_bearer("sct_not-a-real-token"))).status_code == 401


# --- Roles still apply -------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_can_only_issue_read_tokens(client: AsyncClient, viewer_auth_headers: dict):
    resp = await client.post(
        "/api/access-tokens", headers=viewer_auth_headers, json={"name": "x", "scopes": ["categories:write"]}
    )
    assert resp.status_code == 422
    await _create(client, viewer_auth_headers, ["categories:read"])


@pytest.mark.asyncio
async def test_demoted_member_loses_write_even_with_a_write_scope(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user, test_workspace
):
    token = (await _create(client, auth_headers, ["categories:write"]))["token"]
    member = (
        await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.user_id == test_user.id, WorkspaceMember.workspace_id == test_workspace.id
            )
        )
    ).scalar_one()
    member.role = "viewer"
    await session.commit()

    resp = await client.post("/api/categories", headers=_bearer(token), json={"name": "Pets"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_removed_member_loses_access(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user, test_workspace
):
    token = (await _create(client, auth_headers, ["categories:read"]))["token"]
    member = (
        await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.user_id == test_user.id, WorkspaceMember.workspace_id == test_workspace.id
            )
        )
    ).scalar_one()
    await session.delete(member)
    await session.commit()

    assert (await client.get("/api/categories", headers=_bearer(token))).status_code == 404


@pytest.mark.asyncio
async def test_owner_sees_and_revokes_everyone_but_viewer_only_their_own(
    client: AsyncClient, auth_headers: dict, viewer_auth_headers: dict
):
    owner_token = await _create(client, auth_headers, ["categories:read"])
    viewer_token = await _create(client, viewer_auth_headers, ["categories:read"])

    owner_list = (await client.get("/api/access-tokens", headers=auth_headers)).json()
    assert {t["id"] for t in owner_list} == {owner_token["id"], viewer_token["id"]}
    viewer_list = (await client.get("/api/access-tokens", headers=viewer_auth_headers)).json()
    assert [t["id"] for t in viewer_list] == [viewer_token["id"]]

    resp = await client.delete(f"/api/access-tokens/{owner_token['id']}", headers=viewer_auth_headers)
    assert resp.status_code == 404
    resp = await client.delete(f"/api/access-tokens/{viewer_token['id']}", headers=auth_headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_token_runs_in_its_workspace_without_a_header(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user
):
    """A token for a workspace other than the default one still reads that
    workspace when the request names none."""
    other = Workspace(id=uuid.uuid4(), name="Other", kind="personal", default_currency="USD", locale="en")
    session.add(other)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other.id, user_id=test_user.id, role="owner"))
    await session.commit()

    other_headers = {**auth_headers, "X-Workspace-Id": str(other.id)}
    resp = await client.post("/api/categories", headers=other_headers, json={"name": "Only in other"})
    assert resp.status_code == 201, resp.text
    token = (await _create(client, other_headers, ["categories:read"]))["token"]

    names = {c["name"] for c in (await client.get("/api/categories", headers=_bearer(token))).json()}
    assert "Only in other" in names
    default_names = {c["name"] for c in (await client.get("/api/categories", headers=auth_headers)).json()}
    assert "Only in other" not in default_names


@pytest.mark.asyncio
async def test_workspace_header_is_compared_as_a_uuid(client: AsyncClient, auth_headers: dict, test_workspace):
    token = (await _create(client, auth_headers, ["categories:read"]))["token"]
    for spelling in (test_workspace.id.hex, str(test_workspace.id).upper()):
        resp = await client.get("/api/categories", headers=_bearer(token, **{"X-Workspace-Id": spelling}))
        assert resp.status_code == 200, (spelling, resp.text)
    resp = await client.get("/api/categories", headers=_bearer(token, **{"X-Workspace-Id": "not-a-uuid"}))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_scope_is_matched_on_the_routed_path_under_a_root_path(auth_headers: dict, client: AsyncClient):
    """Served under a prefix (`uvicorn --root-path /securo`), the request path
    carries the prefix but scopes are declared on the routed path."""
    from httpx import ASGITransport

    from app.main import app

    token = (await _create(client, auth_headers, ["categories:read"]))["token"]
    transport = ASGITransport(app=app, root_path="/securo")
    async with AsyncClient(transport=transport, base_url="http://test") as prefixed:
        resp = await prefixed.get("/securo/api/categories", headers=_bearer(token))
        assert resp.status_code == 200, resp.text
        resp = await prefixed.get("/securo/api/workspaces", headers=_bearer(token))
        assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_root_path_is_stripped_only_on_a_segment_boundary(auth_headers: dict, client: AsyncClient):
    """A root path of `/ap` must not eat the start of `/api/...`: Starlette
    routes that request as `/api/categories`, so the scope check must too."""
    from httpx import ASGITransport

    from app.main import app

    token = (await _create(client, auth_headers, ["categories:read"]))["token"]
    transport = ASGITransport(app=app, root_path="/ap")
    async with AsyncClient(transport=transport, base_url="http://test") as prefixed:
        resp = await prefixed.get("/api/categories", headers=_bearer(token))
        assert resp.status_code == 200, resp.text
