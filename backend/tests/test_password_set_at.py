"""Whether a person set a password they know (#1965, #1966).

The password column always holds a hash, so it cannot answer that: accounts
born without a typed password hold the hash of a thrown-away secret.
"""

from datetime import datetime, timezone

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import oidc_auth
from app.core.auth import UserManager, _AccountLookupUserDatabase
from app.models.user import User
from app.services import user_removal_service
from tests.test_oidc_auth import oidc_settings  # noqa: F401


@pytest.mark.asyncio
async def test_a_sign_up_with_a_typed_password_stamps_it(client: AsyncClient, session: AsyncSession, clean_db):
    resp = await client.post("/api/auth/register", json={"email": "new@example.com", "password": "newpassword12"})
    assert resp.status_code == 201, resp.text
    user = await session.scalar(select(User).where(User.email == "new@example.com"))
    assert user is not None and user.password_set_at is not None


@pytest.mark.asyncio
async def test_an_account_an_identity_provider_created_has_no_password_set(
    session: AsyncSession, clean_db, oidc_settings  # noqa: F811
):
    # The adapter the app uses, which stamps every other sign-up.
    manager = UserManager(_AccountLookupUserDatabase(session, User))
    user = await oidc_auth._get_or_create_oidc_user(
        {"sub": "sub-1", "email": "idp@example.com", "email_verified": True},
        {},
        "https://id.example.com",
        session,
        manager,
    )
    assert user.hashed_password and user.password_set_at is None


@pytest.mark.asyncio
async def test_changing_the_password_stamps_it(session: AsyncSession, test_user: User):
    from app.schemas.user import UserUpdate

    test_user.password_set_at = None
    await session.commit()
    manager = UserManager(SQLAlchemyUserDatabase(session, User))
    await manager.update(UserUpdate(password="anotherpass12"), test_user)
    await session.refresh(test_user)
    assert test_user.password_set_at is not None


@pytest.mark.asyncio
async def test_an_update_without_a_password_leaves_it(session: AsyncSession, test_user: User):
    from app.schemas.user import UserUpdate

    test_user.password_set_at = None
    await session.commit()
    manager = UserManager(SQLAlchemyUserDatabase(session, User))
    await manager.update(UserUpdate(preferences={"language": "en"}), test_user)
    await session.refresh(test_user)
    assert test_user.password_set_at is None


@pytest.mark.asyncio
async def test_a_removed_account_shell_has_no_password_set(test_user: User):
    test_user.password_set_at = datetime.now(timezone.utc)
    user_removal_service._close(test_user)
    assert test_user.password_set_at is None
