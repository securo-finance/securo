"""An administrator can manage accounts, never become one (ADR 0042).

Two promises are tested here, both from the production audit: the admin API
cannot set somebody's password, and every administrative action on an account
needs a reason and leaves a record the affected side can read.
"""
import uuid

import bcrypt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_jwt_strategy
from app.models.admin_access_log import AdminAccessLog
from app.models.user import User
from app.models.workspace import Workspace

pytestmark = pytest.mark.asyncio

REASON = "Customer wrote in from the support inbox"


async def _user_by_email(session: AsyncSession, email: str) -> User:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one()


async def _log_entries(session: AsyncSession) -> list[AdminAccessLog]:
    result = await session.execute(
        select(AdminAccessLog).order_by(AdminAccessLog.created_at)
    )
    return list(result.scalars().all())


async def _add_user(session: AsyncSession, email: str, password: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _headers_for(user: User) -> dict:
    token = await get_jwt_strategy().write_token(user)
    return {"Authorization": f"Bearer {token}"}


async def test_admin_cannot_set_a_password(
    client: AsyncClient, admin_auth_headers: dict, test_user: User
):
    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "password": "hijack12345"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 422
    fields = [error["loc"][-1] for error in response.json()["detail"]]
    assert "password" in fields

    # The point of the audit: the old password still signs in, and the one the
    # administrator tried to set does not.
    old_login = await client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpass123"},
    )
    assert old_login.status_code == 200
    hijack_login = await client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "hijack12345"},
    )
    assert hijack_login.status_code != 200


async def test_the_users_router_cannot_reach_another_account(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    """The fastapi-users superuser `/{id}` routes were the second door in ADR 0042.

    `PATCH /api/users/{id}` set another account's address, password or flags in
    one request, with no reason and no entry in the access log. Only the `/me`
    routes stay mounted.
    """
    target = f"/api/users/{test_user.id}"
    read = await client.get(target, headers=admin_auth_headers)
    moved = await client.patch(
        target, json={"email": "planted@example.com"}, headers=admin_auth_headers
    )
    changed = await client.patch(
        target, json={"password": "hijack12345"}, headers=admin_auth_headers
    )
    deleted = await client.delete(target, headers=admin_auth_headers)

    assert [r.status_code for r in (read, moved, changed, deleted)] == [404, 404, 404, 404]

    # Nothing moved: the owner's address and password still sign in, and the
    # attempts left no trace because they never reached a handler.
    still_theirs = await client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpass123"},
    )
    assert still_theirs.status_code == 200
    planted = await client.post(
        "/api/auth/login",
        data={"username": "planted@example.com", "password": "hijack12345"},
    )
    assert planted.status_code != 200
    assert await _log_entries(session) == []

    # The caller's own routes are still there.
    assert (await client.get("/api/users/me", headers=admin_auth_headers)).status_code == 200


async def test_update_without_a_reason_is_refused(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"preferences": {"language": "de"}},
        headers=admin_auth_headers,
    )

    assert response.status_code == 422
    fields = [error["loc"][-1] for error in response.json()["detail"]]
    assert "reason" in fields
    await session.refresh(test_user)
    assert (test_user.preferences or {}).get("language") != "de"
    assert await _log_entries(session) == []


async def test_update_is_logged_with_actor_target_and_ip(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "preferences": {"language": "de"}},
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    entries = await _log_entries(session)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor_email == "admin@example.com"
    assert entry.actor_id is not None
    assert entry.target_user_id == test_user.id
    assert entry.target_email == "test@example.com"
    assert entry.action == "user.update"
    assert entry.reason == REASON
    assert entry.ip
    assert entry.created_at is not None


async def test_delete_needs_a_reason(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    response = await client.delete(
        f"/api/admin/users/{test_user.id}", headers=admin_auth_headers
    )

    assert response.status_code == 422
    assert (await _user_by_email(session, "test@example.com")) is not None


async def test_delete_is_logged_and_outlives_the_account(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    response = await client.delete(
        f"/api/admin/users/{test_user.id}",
        params={"reason": REASON},
        headers=admin_auth_headers,
    )

    assert response.status_code == 204
    entries = await _log_entries(session)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "user.delete"
    assert entry.target_email == "test@example.com"
    assert entry.reason == REASON

    listing = await client.get("/api/admin/access-log", headers=admin_auth_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


async def test_admin_log_lists_every_action_newest_first(
    client: AsyncClient, admin_auth_headers: dict, test_user: User
):
    for language in ("de", "fr"):
        patched = await client.patch(
            f"/api/admin/users/{test_user.id}",
            json={"reason": f"Support ticket {language}", "preferences": {"language": language}},
            headers=admin_auth_headers,
        )
        assert patched.status_code == 200

    response = await client.get("/api/admin/access-log", headers=admin_auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["reason"] for item in body["items"]] == [
        "Support ticket fr",
        "Support ticket de",
    ]


async def test_admin_log_is_admin_only(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, auth_headers: dict
):
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "preferences": {"language": "de"}},
        headers=admin_auth_headers,
    )

    response = await client.get("/api/admin/access-log", headers=auth_headers)

    assert response.status_code == 403


async def test_the_affected_account_sees_what_was_done(
    client: AsyncClient, admin_auth_headers: dict, viewer_auth_headers: dict, session: AsyncSession
):
    viewer = await _user_by_email(session, "viewer-role@example.com")
    patched = await client.patch(
        f"/api/admin/users/{viewer.id}",
        json={"reason": REASON, "is_active": True},
        headers=admin_auth_headers,
    )
    assert patched.status_code == 200

    response = await client.get("/api/access-log/mine", headers=viewer_auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["actor_email"] == "admin@example.com"
    assert item["target_email"] == viewer.email
    assert item["reason"] == REASON


async def test_a_workspace_owner_sees_access_to_a_member(
    client: AsyncClient, admin_auth_headers: dict, viewer_auth_headers: dict,
    auth_headers: dict, session: AsyncSession,
):
    # viewer_auth_headers puts the viewer in the test user's workspace.
    viewer = await _user_by_email(session, "viewer-role@example.com")
    patched = await client.patch(
        f"/api/admin/users/{viewer.id}",
        json={"reason": REASON, "preferences": {"language": "en"}},
        headers=admin_auth_headers,
    )
    assert patched.status_code == 200

    response = await client.get("/api/access-log/mine", headers=auth_headers)

    assert response.status_code == 200
    targets = [item["target_email"] for item in response.json()["items"]]
    assert viewer.email in targets


async def test_a_workspace_owner_still_sees_it_after_the_member_is_deleted(
    client: AsyncClient, admin_auth_headers: dict, viewer_auth_headers: dict,
    auth_headers: dict, session: AsyncSession,
):
    """Deleting the member takes the membership, not the record.

    Resolved live, the owner's access would disappear together with the account
    the record is about, which is exactly the case someone would want to check.
    """
    viewer = await _user_by_email(session, "viewer-role@example.com")
    deleted = await client.delete(
        f"/api/admin/users/{viewer.id}",
        params={"reason": REASON},
        headers=admin_auth_headers,
    )
    assert deleted.status_code == 204

    response = await client.get("/api/access-log/mine", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "user.delete"
    assert body["items"][0]["target_email"] == viewer.email


async def test_a_workspace_manager_sees_access_to_a_member(
    client: AsyncClient, admin_auth_headers: dict, viewer_auth_headers: dict,
    test_workspace: Workspace, session: AsyncSession,
):
    """A manager acts with owner rights without being on the roster (ADR 0003).

    Nothing about their access can be read from a membership, so the audience
    has to name them or the person operating the workspace never learns that
    an administrator touched one of its accounts.
    """
    manager = await _add_user(session, "manager@example.com", "managerpass123")
    test_workspace.managed_by_user_id = manager.id
    await session.commit()

    # viewer_auth_headers puts the viewer in the test workspace this user manages.
    viewer = await _user_by_email(session, "viewer-role@example.com")
    patched = await client.patch(
        f"/api/admin/users/{viewer.id}",
        json={"reason": REASON, "preferences": {"language": "en"}},
        headers=admin_auth_headers,
    )
    assert patched.status_code == 200

    response = await client.get("/api/access-log/mine", headers=await _headers_for(manager))

    assert response.status_code == 200
    targets = [item["target_email"] for item in response.json()["items"]]
    assert viewer.email in targets


async def test_a_workspace_owner_sees_access_to_the_account_that_manages_it(
    client: AsyncClient, admin_auth_headers: dict, auth_headers: dict,
    test_user: User, test_workspace: Workspace, session: AsyncSession,
):
    """The manager rule read the other way.

    Someone who manages a workspace holds owner rights over it (ADR 0003), so
    that person's account can read the workspace. An administrator who reached
    that account reached the workspace, and its owner is entitled to know.
    """
    manager = await _add_user(session, "managing@example.com", "managerpass123")
    test_workspace.managed_by_user_id = manager.id
    await session.commit()

    patched = await client.patch(
        f"/api/admin/users/{manager.id}",
        json={"reason": REASON, "preferences": {"language": "en"}},
        headers=admin_auth_headers,
    )
    assert patched.status_code == 200

    # test_user owns test_workspace and is not on the roster of anything the
    # manager is a member of: only the reverse rule can let them see this.
    response = await client.get("/api/access-log/mine", headers=auth_headers)

    assert response.status_code == 200
    targets = [item["target_email"] for item in response.json()["items"]]
    assert manager.email in targets


async def test_someone_else_sees_nothing(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "preferences": {"language": "de"}},
        headers=admin_auth_headers,
    )

    stranger = await _add_user(session, "stranger@example.com", "strangerpass123")

    response = await client.get("/api/access-log/mine", headers=await _headers_for(stranger))

    assert response.status_code == 200
    assert response.json()["total"] == 0
