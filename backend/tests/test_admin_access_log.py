"""An administrator can manage accounts, never become one (ADR 0042).

Two promises are tested here, both from the production audit: the admin API
cannot set somebody's password, and every administrative action on an account
needs a reason and leaves a record the affected side can read.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_jwt_strategy
from app.models.admin_access_log import AdminAccessLog
from app.models.user import User

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


async def test_someone_else_sees_nothing(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": REASON, "preferences": {"language": "de"}},
        headers=admin_auth_headers,
    )

    import bcrypt as _bcrypt

    stranger = User(
        id=uuid.uuid4(),
        email="stranger@example.com",
        hashed_password=_bcrypt.hashpw(b"strangerpass123", _bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(stranger)
    await session.commit()
    token = await get_jwt_strategy().write_token(stranger)

    response = await client.get(
        "/api/access-log/mine", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0
