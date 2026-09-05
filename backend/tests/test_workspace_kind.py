"""Workspace.kind as a real, validated, write-once field.

`kind` is chosen at creation and never edited afterwards, so the tests
cover the create-side validation, the fact that update cannot touch it,
and the bootstrap idempotency guard that identifies the first workspace
by who made it rather than by its kind.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.user import User
from app.models.workspace import WORKSPACE_KINDS, Workspace, WorkspaceMember
from app.services import workspace_service


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
def test_only_two_kinds_exist():
    assert WORKSPACE_KINDS == ("personal", "business")


@pytest.mark.asyncio
async def test_create_rejects_unknown_kind(client: AsyncClient, auth_headers, test_user):
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Nonsense", "kind": "nonsense", "self_membership": True},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("retired", ["freelancer", "small_business", "accountant_firm"])
async def test_create_rejects_retired_kinds(
    client: AsyncClient, auth_headers, test_user, retired: str
):
    """The work kinds collapsed into `business`; accountant_firm was an
    edge between workspaces, which `managed_by_user_id` already covers."""
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Old kind", "kind": retired, "self_membership": True},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_persists_selected_kind(client: AsyncClient, auth_headers, test_user):
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Consultoria", "kind": "business", "self_membership": True},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "business"

    # And it survives the round trip through the listing.
    listing = await client.get("/api/workspaces", headers=auth_headers)
    created = next(w for w in listing.json() if w["name"] == "Consultoria")
    assert created["kind"] == "business"


@pytest.mark.asyncio
async def test_create_defaults_to_personal(client: AsyncClient, auth_headers, test_user):
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Unspecified", "self_membership": True},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "personal"


# ---------------------------------------------------------------------------
# update — kind is write-once
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_cannot_change_kind(client: AsyncClient, auth_headers, test_workspace):
    """`kind` is not part of WorkspaceUpdate, so a client that sends it
    gets everything else applied and the kind left alone."""
    resp = await client.patch(
        f"/api/workspaces/{test_workspace.id}",
        headers=auth_headers,
        json={"name": "Renamed", "kind": "business"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["kind"] == "personal"

    # Not just the response shape — the row itself is untouched.
    current = await client.get("/api/workspaces/current", headers=auth_headers)
    assert current.json()["kind"] == "personal"


# ---------------------------------------------------------------------------
# bootstrap idempotency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(session, test_user: User, test_workspace: Workspace):
    again = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()
    assert again.id == test_workspace.id

    rows = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == test_user.id)
    )
    assert len(list(rows.scalars().all())) == 1


@pytest.mark.asyncio
async def test_bootstrap_returns_the_oldest_workspace(
    session, test_user: User, test_workspace: Workspace
):
    """A second self-owned workspace doesn't shadow the bootstrap one,
    whatever kind either of them has."""
    await workspace_service.create_workspace(
        session,
        name="Second",
        creator=test_user,
        kind="business",
        self_membership=True,
        seed_defaults=False,
    )
    await session.commit()

    found = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()
    assert found.id == test_workspace.id


@pytest.mark.asyncio
async def test_bootstrap_ignores_a_hand_made_personal_workspace(
    session, test_user: User, test_workspace: Workspace
):
    """Matching on `kind == "personal"` would have made this ambiguous."""
    await workspace_service.create_workspace(
        session,
        name="Second home",
        creator=test_user,
        kind="personal",
        self_membership=True,
        seed_defaults=False,
    )
    await session.commit()

    found = await workspace_service.create_personal_workspace_for_user(session, test_user)
    await session.commit()
    assert found.id == test_workspace.id


@pytest.mark.asyncio
async def test_fresh_signup_lands_in_a_personal_workspace(client: AsyncClient, clean_db):
    """First run is untouched: no question asked, kind is personal."""
    resp = await client.post(
        "/api/auth/register",
        json={"email": "brandnew@example.com", "password": "newpass123"},
    )
    assert resp.status_code == 201, resp.text

    login = await client.post(
        "/api/auth/login",
        data={"username": "brandnew@example.com", "password": "newpass123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    current = await client.get("/api/workspaces/current", headers=headers)
    assert current.status_code == 200, current.text
    assert current.json()["kind"] == "personal"
