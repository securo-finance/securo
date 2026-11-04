from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from starlette.requests import Request

from app.core.rate_limit import resolve_client_ip


def _request(client_host, forwarded_for=None):
    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
    scope = {
        "type": "http",
        "client": (client_host, 12345) if client_host else None,
        "headers": headers,
    }
    return Request(scope)


def test_resolve_client_ip_untrusted_uses_socket_peer():
    """trusted_proxy_hops=0 (default) ignores X-Forwarded-For entirely."""
    request = _request("172.26.0.3", forwarded_for="1.2.3.4")
    assert resolve_client_ip(request, trusted_proxy_hops=0) == "172.26.0.3"


def test_resolve_client_ip_no_socket_peer_is_unknown():
    request = _request(None)
    assert resolve_client_ip(request, trusted_proxy_hops=0) == "unknown"


def test_resolve_client_ip_one_trusted_hop():
    """One trusted proxy (nginx): the header it set is the real client."""
    request = _request("172.26.0.3", forwarded_for="203.0.113.9")
    assert resolve_client_ip(request, trusted_proxy_hops=1) == "203.0.113.9"


def test_resolve_client_ip_ignores_client_supplied_prefix():
    """A client-forged leading entry must not override the trusted hop."""
    request = _request("172.26.0.3", forwarded_for="1.1.1.1, 203.0.113.9")
    assert resolve_client_ip(request, trusted_proxy_hops=1) == "203.0.113.9"


def test_resolve_client_ip_two_trusted_hops():
    request = _request("172.26.0.3", forwarded_for="203.0.113.9, 10.0.0.5")
    assert resolve_client_ip(request, trusted_proxy_hops=2) == "203.0.113.9"


def test_resolve_client_ip_short_chain_is_unknown():
    """Fewer hops than configured means the trust setting doesn't match reality."""
    request = _request("172.26.0.3", forwarded_for="10.0.0.5")
    assert resolve_client_ip(request, trusted_proxy_hops=2) == "unknown"


def test_resolve_client_ip_missing_header_is_unknown():
    request = _request("172.26.0.3")
    assert resolve_client_ip(request, trusted_proxy_hops=1) == "unknown"


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
