from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from starlette.requests import Request

from app.core.config import Settings
from app.core.rate_limit import resolve_client_ip
from app.main import app


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


# --- Only a trusted proxy's X-Forwarded-For is read (ADR 0024) ---------------
#
# Two topologies are shipped: nginx alone in front of the backend
# (TRUSTED_PROXY_HOPS=1) and Traefik in front of that nginx (=2). In both, a
# caller who reaches a port behind the outer proxy directly must not be able
# to write its own identity into the header.


def test_direct_caller_cannot_pick_its_address_with_the_header():
    """A public peer on the backend port is the client, whatever it forwards."""
    request = _request("198.51.100.7", forwarded_for="1.2.3.4")
    assert resolve_client_ip(request, trusted_proxy_hops=1) == "198.51.100.7"


def test_direct_caller_cannot_pick_its_address_behind_traefik_setting():
    request = _request("198.51.100.7", forwarded_for="1.2.3.4, 10.0.0.5")
    assert resolve_client_ip(request, trusted_proxy_hops=2) == "198.51.100.7"


def test_caller_skipping_traefik_is_keyed_by_the_address_nginx_saw():
    """With hops=2, a request that reaches nginx without Traefik carries one
    real hop. The forged entry left of it must not become the key."""
    request = _request("172.26.0.3", forwarded_for="1.2.3.4, 198.51.100.7")
    assert resolve_client_ip(request, trusted_proxy_hops=2) == "198.51.100.7"


def test_traefik_in_front_of_nginx_reaches_the_visitor():
    request = _request("172.26.0.3", forwarded_for="1.2.3.4, 203.0.113.9, 172.30.0.2")
    assert resolve_client_ip(request, trusted_proxy_hops=2) == "203.0.113.9"


def test_trusted_networks_can_be_narrowed():
    """Only the networks configured are proxies: a private peer outside them
    is a client like any other."""
    request = _request("192.168.1.20", forwarded_for="1.2.3.4")
    assert (
        resolve_client_ip(request, trusted_proxy_hops=1, trusted_proxy_networks="172.26.0.0/16")
        == "192.168.1.20"
    )
    request = _request("172.26.0.3", forwarded_for="203.0.113.9")
    assert (
        resolve_client_ip(request, trusted_proxy_hops=1, trusted_proxy_networks="172.26.0.0/16")
        == "203.0.113.9"
    )


def test_trusted_proxy_networks_setting_rejects_a_typo():
    with pytest.raises(ValidationError, match="TRUSTED_PROXY_NETWORKS"):
        Settings(trusted_proxy_networks="10.0.0.0/8,not-a-network", _env_file=None, _secrets_dir=None)


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


async def _login_attempts(peer: str, forwarded_for: list[str], hops: int) -> list[int]:
    """One failed login per X-Forwarded-For value, all from the same socket peer."""
    settings = Settings(trusted_proxy_hops=hops, _env_file=None, _secrets_dir=None)
    transport = ASGITransport(app=app, client=(peer, 40000))
    statuses = []
    with patch("app.core.rate_limit.get_settings", return_value=settings):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for value in forwarded_for:
                response = await client.post(
                    "/api/auth/login",
                    data={"username": "nobody@example.com", "password": "wrong"},
                    headers={"X-Forwarded-For": value},
                )
                statuses.append(response.status_code)
    return statuses


@pytest.mark.parametrize(
    ("hops", "visitor_a", "visitor_b"),
    [
        (1, "203.0.113.9", "198.51.100.7"),
        (2, "203.0.113.9, 172.30.0.2", "198.51.100.7, 172.30.0.2"),
    ],
    ids=["nginx", "traefik-then-nginx"],
)
async def test_two_visitors_behind_the_proxies_get_separate_buckets(hops, visitor_a, visitor_b):
    """nginx (172.26.0.3) is the socket peer; with Traefik, 172.30.0.2 is Traefik."""
    first = await _login_attempts("172.26.0.3", [visitor_a] * 6, hops)
    assert first[-1] == 429
    assert await _login_attempts("172.26.0.3", [visitor_b], hops) == [400]


@pytest.mark.parametrize("hops", [1, 2], ids=["nginx", "traefik-then-nginx"])
async def test_direct_caller_rotating_the_header_stays_in_one_bucket(hops):
    forged = [f"1.2.3.{n}, 10.0.0.{n}" for n in range(6)]
    statuses = await _login_attempts("198.51.100.7", forged, hops)
    assert statuses == [400] * 5 + [429]
