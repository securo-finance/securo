"""A reason is what makes an administrative action defensible (ADR 0042, #557).

The screen already refuses a reason of spaces, so the API must not accept one
either: otherwise the record exists but explains nothing. A NUL byte is not
text PostgreSQL can store, so it used to reach the driver and answer 500
instead of refusing the input like every other malformed field.
"""
import uuid

import bcrypt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_access_log import AdminAccessLog
from app.models.user import User

pytestmark = pytest.mark.asyncio

REASON = "Customer wrote in from the support inbox"
BLANK_REASONS = ["   ", "\t\n ", "    "]


async def _log_entries(session: AsyncSession) -> list[AdminAccessLog]:
    result = await session.execute(select(AdminAccessLog))
    return list(result.scalars().all())


@pytest.mark.parametrize("reason", BLANK_REASONS)
async def test_an_update_without_actual_words_is_refused(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession,
    reason: str,
):
    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": reason, "preferences": {"language": "de"}},
        headers=admin_auth_headers,
    )

    assert response.status_code == 422
    assert [error["loc"][-1] for error in response.json()["detail"]] == ["reason"]
    await session.refresh(test_user)
    assert (test_user.preferences or {}).get("language") != "de"
    assert await _log_entries(session) == []


@pytest.mark.parametrize("reason", BLANK_REASONS)
async def test_a_delete_without_actual_words_is_refused(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession,
    reason: str,
):
    response = await client.delete(
        f"/api/admin/users/{test_user.id}",
        params={"reason": reason},
        headers=admin_auth_headers,
    )

    assert response.status_code == 422
    assert (await session.get(User, test_user.id)) is not None
    assert await _log_entries(session) == []


async def test_an_update_with_a_nul_byte_is_refused_not_a_crash(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    response = await client.patch(
        f"/api/admin/users/{test_user.id}",
        json={"reason": "Support ticket \x00 42", "preferences": {"language": "de"}},
        headers=admin_auth_headers,
    )

    assert response.status_code == 422
    assert [error["loc"][-1] for error in response.json()["detail"]] == ["reason"]
    assert await _log_entries(session) == []


async def test_a_delete_with_a_nul_byte_is_refused_not_a_crash(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    response = await client.delete(
        f"/api/admin/users/{test_user.id}",
        params={"reason": "Support ticket \x00 42"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 422
    assert (await session.get(User, test_user.id)) is not None
    assert await _log_entries(session) == []


async def test_the_reason_is_stored_trimmed_and_keeps_its_number(
    client: AsyncClient, admin_auth_headers: dict, test_user: User, session: AsyncSession
):
    """A number is not passed through a validator that breaks on it.

    ``TestClient`` numbers aside, the stored text has to be the words the
    administrator typed, without the padding around them.
    """
    response = await client.delete(
        f"/api/admin/users/{test_user.id}",
        params={"reason": "  Support ticket 4242  "},
        headers=admin_auth_headers,
    )

    assert response.status_code == 204
    entries = await _log_entries(session)
    assert len(entries) == 1
    assert entries[0].reason == "Support ticket 4242"


async def test_a_reason_of_only_the_minimum_is_still_accepted(
    client: AsyncClient, admin_auth_headers: dict, session: AsyncSession
):
    """Three characters are the floor, and the floor still works."""
    user = User(
        id=uuid.uuid4(),
        email="short-reason@example.com",
        hashed_password=bcrypt.hashpw(b"somepass123", bcrypt.gensalt()).decode(),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()

    response = await client.patch(
        f"/api/admin/users/{user.id}",
        json={"reason": "abc", "preferences": {"language": "de"}},
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
