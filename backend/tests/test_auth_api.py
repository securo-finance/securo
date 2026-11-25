import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import UserManager, get_jwt_strategy, get_user_db
from app.models.user import User


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
async def test_email_lookup_ignores_case(session: AsyncSession, test_user: User):
    """Sign-in and recovery resolve the address through this lookup.

    An account stored as `Test@example.com` must be found when the person
    types `test@example.com`, or a capital reads as a wrong password (#556).
    """
    manager = UserManager(await anext(get_user_db(session)))

    found = await manager.get_by_email("TEST@EXAMPLE.COM")
    assert found.id == test_user.id


@pytest.mark.asyncio
async def test_login_and_registration_ignore_the_case_of_the_address(client: AsyncClient, clean_db):
    registered = await client.post(
        "/api/auth/register",
        json={"email": "Test@example.com", "password": "casepass123"},
    )
    assert registered.status_code == 201

    for typed in ("test@example.com", "TEST@EXAMPLE.COM"):
        login = await client.post(
            "/api/auth/login",
            data={"username": typed, "password": "casepass123"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert login.status_code == 200, f"{typed} did not find the account"

    # The duplicate check uses the same lookup: a second account differing
    # only in case is refused instead of leaving two accounts ambiguous.
    duplicate = await client.post(
        "/api/auth/register",
        json={"email": "test@example.com", "password": "otherpass123"},
    )
    assert duplicate.status_code == 400


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
async def test_openapi_token_url_is_absolute(client: AsyncClient):
    response = await client.get("/api/openapi.json")
    flows = response.json()["components"]["securitySchemes"]["OAuth2PasswordBearer"]["flows"]
    assert flows["password"]["tokenUrl"] == "/api/auth/login"


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
