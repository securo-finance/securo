from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pyotp
import pytest
from httpx import AsyncClient

from app.api.two_factor import _verify_totp
from app.core.auth import get_jwt_strategy


def _make_redis_mock_with_store():
    """Create a Redis mock that actually stores and retrieves key-value pairs."""
    store = {}
    mock = AsyncMock()

    async def mock_get(key):
        return store.get(key)

    async def mock_set(key, value, ex=None):
        store[key] = value

    async def mock_delete(key):
        store.pop(key, None)

    mock.get = AsyncMock(side_effect=mock_get)
    mock.set = AsyncMock(side_effect=mock_set)
    mock.delete = AsyncMock(side_effect=mock_delete)

    # Pipeline for rate limiter (always allow)
    pipe_mock = MagicMock()
    pipe_mock.execute = AsyncMock(return_value=[0, 0, True, True])
    mock.pipeline = lambda: pipe_mock

    return mock


@pytest.fixture(autouse=True)
def _override_redis_with_store(_mock_redis):
    """Override the autouse _mock_redis with one that has a real key-value store."""
    mock = _make_redis_mock_with_store()

    async def _fake():
        return mock

    with patch("app.core.redis.get_redis", _fake), \
         patch("app.core.rate_limit.get_redis", _fake), \
         patch("app.api.custom_auth.get_redis", _fake), \
         patch("app.api.two_factor.get_redis", _fake):
        yield mock


async def test_setup_2fa(client: AsyncClient, auth_headers: dict):
    response = await client.post("/api/auth/2fa/setup", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "secret" in data
    assert "otpauth_uri" in data
    assert "otpauth://totp/" in data["otpauth_uri"]


@pytest.mark.parametrize("path", ["/api/auth/2fa/setup", "/api/auth/2fa/enable"])
async def test_totp_enrollment_forbidden_when_local_auth_disabled(
    client: AsyncClient,
    test_user,
    oidc_only_settings,
    path: str,
):
    token = await get_jwt_strategy().write_token(test_user)
    response = await client.post(
        path,
        json={"code": "123456"} if path.endswith("/enable") else None,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "LOCAL_AUTH_DISABLED"


async def test_disable_2fa_allowed_for_cleanup_when_local_auth_disabled(
    client: AsyncClient,
    test_user,
    session,
    oidc_only_settings,
):
    secret = pyotp.random_base32()
    test_user.totp_secret = secret
    test_user.is_2fa_enabled = True
    session.add(test_user)
    await session.commit()

    token = await get_jwt_strategy().write_token(test_user)
    response = await client.post(
        "/api/auth/2fa/disable",
        json={"password": "testpass123", "code": pyotp.TOTP(secret).now()},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["detail"] == "2FA disabled"


async def test_enable_2fa(client: AsyncClient, auth_headers: dict, test_user):
    # First setup
    setup_resp = await client.post("/api/auth/2fa/setup", headers=auth_headers)
    secret = setup_resp.json()["secret"]

    # Generate valid code
    totp = pyotp.TOTP(secret)
    code = totp.now()

    response = await client.post(
        "/api/auth/2fa/enable",
        json={"code": code},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["detail"] == "2FA enabled"


async def test_enable_2fa_invalid_code(client: AsyncClient, auth_headers: dict, test_user):
    # First setup
    await client.post("/api/auth/2fa/setup", headers=auth_headers)

    response = await client.post(
        "/api/auth/2fa/enable",
        json={"code": "000000"},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_verify_totp_allows_adjacent_time_windows():
    secret = pyotp.random_base32()
    fixed_time = datetime(2026, 7, 19, 12, 0)
    totp = pyotp.TOTP(secret)
    previous_code = totp.at(fixed_time, counter_offset=-1)
    next_code = totp.at(fixed_time, counter_offset=1)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 19, 12, 0, tzinfo=tz)

    with patch("pyotp.totp.datetime.datetime", FrozenDateTime):
        assert _verify_totp(secret, previous_code)
        assert _verify_totp(secret, next_code)


async def test_login_with_2fa(client: AsyncClient, test_user_with_2fa):
    response = await client.post(
        "/api/auth/login",
        data={"username": "2fa@example.com", "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["requires_2fa"] is True
    assert "temp_token" in data


async def test_verify_2fa(client: AsyncClient, test_user_with_2fa):
    # Login to get temp token
    login_resp = await client.post(
        "/api/auth/login",
        data={"username": "2fa@example.com", "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    temp_token = login_resp.json()["temp_token"]

    # Generate valid TOTP code
    totp = pyotp.TOTP(test_user_with_2fa.totp_secret)
    code = totp.now()

    response = await client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": temp_token, "code": code},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


async def test_verify_2fa_invalid_code(client: AsyncClient, test_user_with_2fa):
    # Login to get temp token
    login_resp = await client.post(
        "/api/auth/login",
        data={"username": "2fa@example.com", "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    temp_token = login_resp.json()["temp_token"]

    response = await client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": temp_token, "code": "000000"},
    )
    assert response.status_code == 400


async def test_verify_2fa_expired_token(client: AsyncClient):
    response = await client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": "nonexistent-token", "code": "123456"},
    )
    assert response.status_code == 401


async def test_disable_2fa(client: AsyncClient, test_user_with_2fa):
    # Get auth token for 2FA user by verifying 2FA
    login_resp = await client.post(
        "/api/auth/login",
        data={"username": "2fa@example.com", "password": "testpass123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    temp_token = login_resp.json()["temp_token"]
    totp = pyotp.TOTP(test_user_with_2fa.totp_secret)
    code = totp.now()

    verify_resp = await client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": temp_token, "code": code},
    )
    token = verify_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Now disable 2FA
    new_code = totp.now()
    response = await client.post(
        "/api/auth/2fa/disable",
        json={"password": "testpass123", "code": new_code},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["detail"] == "2FA disabled"


@pytest.mark.asyncio
async def test_enable_2fa_without_setup(client: AsyncClient, auth_headers):
    resp = await client.post(
        "/api/auth/2fa/enable",
        json={"code": "123456"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "setup" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_2fa_invalid_token(client: AsyncClient):
    resp = await client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": "fake-token", "code": "123456"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_verify_2fa_with_valid_token(client: AsyncClient, test_user_with_2fa):
    mock_r = AsyncMock()
    mock_r.get = AsyncMock(return_value=str(test_user_with_2fa.id))
    mock_r.delete = AsyncMock()
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=[0, 0, True, True])
    mock_r.pipeline = lambda: pipe

    async def _fake():
        return mock_r

    with patch("app.api.two_factor.get_redis", _fake), \
         patch("app.core.rate_limit.get_redis", _fake), \
         patch("app.api.custom_auth.get_redis", _fake):
        totp = pyotp.TOTP(test_user_with_2fa.totp_secret)
        resp = await client.post(
            "/api/auth/2fa/verify",
            json={"temp_token": "valid-temp-token", "code": totp.now()},
        )
    assert resp.status_code == 200
    assert "access_token" in resp.json()
