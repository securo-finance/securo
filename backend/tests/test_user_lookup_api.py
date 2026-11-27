import uuid

import pytest

from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


async def _user_with_own_workspace(session, email: str) -> tuple[User, Workspace]:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password="unused",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    workspace = Workspace(id=uuid.uuid4(), name=email, kind="personal", created_by_user_id=user.id)
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=workspace.id, user_id=user.id, role="owner"))
    await session.commit()
    return user, workspace


@pytest.mark.asyncio
async def test_lookup_existing_user(client, auth_headers, test_user):
    resp = await client.get(
        "/api/users/lookup",
        params={"email": test_user.email},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"].lower() == test_user.email.lower()
    assert body["id"] == str(test_user.id)


@pytest.mark.asyncio
async def test_lookup_case_insensitive(client, auth_headers, test_user):
    resp = await client.get(
        "/api/users/lookup",
        params={"email": test_user.email.upper()},
        headers=auth_headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_lookup_missing_user(client, auth_headers, test_user):
    resp = await client.get(
        "/api/users/lookup",
        params={"email": "nobody@example.org"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_lookup_requires_auth(client):
    resp = await client.get(
        "/api/users/lookup", params={"email": "anyone@example.com"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_lookup_returns_the_users_display_name(
    client, auth_headers, session, test_user
):
    """A caller linking a member gets the person's name, not only the email."""
    test_user.preferences = {**(test_user.preferences or {}), "display_name": "User Name"}
    session.add(test_user)
    await session.commit()

    resp = await client.get(
        "/api/users/lookup",
        params={"email": test_user.email},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["display_name"] == "User Name"


@pytest.mark.asyncio
async def test_lookup_reports_no_display_name_when_the_user_has_none(
    client, auth_headers, test_user
):
    resp = await client.get(
        "/api/users/lookup",
        params={"email": test_user.email},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["display_name"] is None


@pytest.mark.asyncio
async def test_directory_survives_a_display_name_that_is_not_a_string(
    client, auth_headers, session, test_user
):
    """Preferences is a JSON column, so a value of the wrong type must not
    take the whole directory down for everyone."""
    test_user.preferences = {**(test_user.preferences or {}), "display_name": 123}
    session.add(test_user)
    await session.commit()

    resp = await client.get("/api/users/directory", headers=auth_headers)

    assert resp.status_code == 200
    entry = next(u for u in resp.json() if u["id"] == str(test_user.id))
    assert entry["display_name"] is None


@pytest.mark.asyncio
async def test_directory_returns_display_names(client, auth_headers, session, test_user):
    """The member picker needs the name of every user it offers."""
    test_user.preferences = {**(test_user.preferences or {}), "display_name": "User Name"}
    session.add(test_user)
    await session.commit()

    resp = await client.get("/api/users/directory", headers=auth_headers)

    assert resp.status_code == 200
    entry = next(u for u in resp.json() if u["id"] == str(test_user.id))
    assert entry["email"] == test_user.email
    assert entry["display_name"] == "User Name"


@pytest.mark.asyncio
async def test_directory_hides_users_who_share_no_workspace(
    client, auth_headers, session, test_user
):
    """The directory must not list every account on the instance (#1173)."""
    stranger, _ = await _user_with_own_workspace(session, "stranger@example.org")

    resp = await client.get("/api/users/directory", headers=auth_headers)

    assert resp.status_code == 200
    ids = {u["id"] for u in resp.json()}
    assert str(test_user.id) in ids
    assert str(stranger.id) not in ids


@pytest.mark.asyncio
async def test_directory_lists_members_of_a_shared_workspace(
    client, auth_headers, session, test_user, test_workspace
):
    partner, _ = await _user_with_own_workspace(session, "partner@example.org")
    session.add(WorkspaceMember(
        id=uuid.uuid4(), workspace_id=test_workspace.id, user_id=partner.id, role="viewer",
    ))
    await session.commit()

    resp = await client.get("/api/users/directory", headers=auth_headers)

    assert resp.status_code == 200
    emails = [u["email"] for u in resp.json()]
    assert emails == sorted(emails)
    assert emails.count("partner@example.org") == 1


@pytest.mark.asyncio
async def test_lookup_still_finds_a_user_outside_the_callers_workspaces(
    client, auth_headers, session, test_user
):
    """Inviting someone from outside still works by typing their exact email."""
    stranger, _ = await _user_with_own_workspace(session, "stranger@example.org")

    resp = await client.get(
        "/api/users/lookup",
        params={"email": "Stranger@example.org"},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["id"] == str(stranger.id)
