from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def _override_redis_with_counter(_mock_redis):
    """Override the autouse _mock_redis with one that counts requests for rate limiting."""
    counters = {}

    def make_pipeline():
        pipe = AsyncMock()
        _path = [None]

        # Pipeline methods are called synchronously (NOT awaited), so use plain functions
        def capture_key(key, *args):
            _path[0] = key

        pipe.zremrangebyscore = capture_key
        pipe.zcard = lambda *a: None
        pipe.zadd = lambda *a, **kw: None
        pipe.expire = lambda *a: None

        async def execute():
            key = _path[0]
            count = counters.get(key, 0)  # count BEFORE this request (zcard result)
            counters[key] = count + 1     # zadd increments
            return [0, count, True, True]

        pipe.execute = AsyncMock(side_effect=execute)
        return pipe

    mock = AsyncMock()
    mock.pipeline = make_pipeline
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock()
    mock.delete = AsyncMock()

    async def _fake():
        return mock

    with patch("app.core.redis.get_redis", _fake), \
         patch("app.core.rate_limit.get_redis", _fake), \
         patch("app.api.custom_auth.get_redis", _fake), \
         patch("app.api.two_factor.get_redis", _fake):
        yield counters


async def test_login_rate_limit(client: AsyncClient, test_user):
    """After 5 failed attempts, the 6th should get 429."""
    for i in range(5):
        response = await client.post(
            "/api/auth/login",
            data={"username": "test@example.com", "password": "wrongpassword"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # Should get 400 (bad credentials), not 429
        assert response.status_code == 400, f"Request {i+1} got {response.status_code}"

    # 6th request should be rate limited
    response = await client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "wrongpassword"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 429
    assert "Retry-After" in response.headers


async def test_totp_verify_rate_limit(client: AsyncClient):
    for i in range(5):
        response = await client.post(
            "/api/auth/2fa/verify",
            json={"temp_token": "invalid", "code": "000000"},
        )
        assert response.status_code == 401, f"Request {i + 1} got {response.status_code}"

    response = await client.post(
        "/api/auth/2fa/verify",
        json={"temp_token": "invalid", "code": "000000"},
    )
    assert response.status_code == 429
    assert "Retry-After" in response.headers
