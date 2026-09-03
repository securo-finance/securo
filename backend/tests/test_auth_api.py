import pytest
from httpx import AsyncClient

from app.core.auth import get_jwt_strategy


@pytest.mark.asyncio
async def test_register(client: AsyncClient, clean_db):
    response = await client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": "newpass123"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "new@example.com"
    assert "id" in data
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient, test_user):
    response = await client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "password": "otherpass123"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_login(client: AsyncClient, test_user):
    response = await client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, test_user):
    response = await client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "wrongpass"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_me(client: AsyncClient, auth_headers):
    response = await client.get("/api/users/me", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["preferences"]["language"] == "pt-BR"


@pytest.mark.asyncio
async def test_get_me_unauthenticated(client: AsyncClient, clean_db):
    response = await client.get("/api/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_forbidden_when_local_auth_disabled(client: AsyncClient, test_user, oidc_only_settings):
    response = await client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "LOCAL_AUTH_DISABLED"


@pytest.mark.asyncio
async def test_register_forbidden_when_local_auth_disabled(client: AsyncClient, clean_db, oidc_only_settings):
    response = await client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": "newpass123"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "LOCAL_AUTH_DISABLED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/auth/forgot-password", {"email": "test@example.com"}),
        ("/api/auth/reset-password", {"token": "invalid", "password": "newpass123"}),
    ],
)
async def test_password_reset_forbidden_when_local_auth_disabled(
    client: AsyncClient,
    clean_db,
    oidc_only_settings,
    path: str,
    payload: dict[str, str],
):
    response = await client.post(path, json=payload)

    assert response.status_code == 403
    assert response.json()["detail"] == "LOCAL_AUTH_DISABLED"


@pytest.mark.asyncio
async def test_self_password_update_forbidden_when_local_auth_disabled(
    client: AsyncClient,
    test_user,
    oidc_only_settings,
):
    token = await get_jwt_strategy().write_token(test_user)
    response = await client.patch(
        "/api/users/me",
        json={"password": "replacement123"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "LOCAL_AUTH_DISABLED"


@pytest.mark.asyncio
async def test_profile_update_allowed_when_local_auth_disabled(
    client: AsyncClient,
    test_user,
    oidc_only_settings,
):
    token = await get_jwt_strategy().write_token(test_user)
    response = await client.patch(
        "/api/users/me",
        json={"preferences": {"language": "en"}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["preferences"]["language"] == "en"
