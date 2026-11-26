import asyncio
import base64
import hashlib
import json
import logging
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import UserAlreadyExists
from pydantic import SecretStr
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import oidc_auth
from app.core.auth import UserManager
from app.core.config import get_settings
from app.models.account import Account
from app.models.app_settings import AppSetting
from app.models.rule import Rule
from app.models.user import User
from app.models.workspace import WorkspaceMember
from app.services.workspace_service import create_personal_workspace_for_user

# The oidc_settings fixture replaces _discover; tests that need the real one
# put this back.
_real_discover = oidc_auth._discover


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def oidc_settings(monkeypatch):
    settings = get_settings()
    old = settings.model_dump()
    settings.oidc_enabled = True
    settings.oidc_provider_name = "Pocket ID"
    settings.oidc_discovery_url = "https://id.example.com/.well-known/openid-configuration"
    settings.oidc_client_id = "securo"
    settings.oidc_client_secret = SecretStr("secret")
    settings.frontend_url = "http://test"
    settings.oidc_sync_roles = False
    settings.oidc_roles_claim = "groups"
    settings.oidc_admin_roles = ""
    settings.oidc_workspace_role_map = ""
    # /config now reads the discovery document for end_session_endpoint
    # (#1623); keep the suite off the network. Tests that need a specific
    # document patch _discover after this.
    async def _empty_discovery():
        return {}

    monkeypatch.setattr(oidc_auth, "_discover", _empty_discovery)
    yield settings
    for key, value in old.items():
        setattr(settings, key, value)


@pytest.mark.asyncio
async def test_decode_id_token_accepts_google_style_at_hash(monkeypatch, oidc_settings):
    """Google id_token carrying at_hash must validate (regression for the
    'Invalid OIDC id_token' failure: jwt.decode got no access_token)."""
    import time

    from cryptography.hazmat.primitives.asymmetric import rsa
    from jose import jwt as jose_jwt
    from jose.backends.cryptography_backend import CryptographyRSAKey
    from jose.constants import ALGORITHMS

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_jwk = CryptographyRSAKey(private, ALGORITHMS.RS256).to_dict()
    pub_jwk = CryptographyRSAKey(private.public_key(), ALGORITHMS.RS256).to_dict()
    pub_jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})

    access_token = "ya29.google-access-token"
    issuer = "https://accounts.google.com"
    now = int(time.time())
    id_token = jose_jwt.encode(
        {
            "iss": issuer,
            "aud": oidc_settings.oidc_client_id,
            "sub": "google-sub-123",
            "email": "user@gmail.com",
            "email_verified": True,
            "nonce": "nonce123",
            "iat": now,
            "exp": now + 600,
        },
        priv_jwk,
        algorithm="RS256",
        headers={"kid": "test-key"},
        access_token=access_token,
    )
    assert "at_hash" in jose_jwt.get_unverified_claims(id_token)

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"keys": [pub_jwk]}

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            return _Resp()

    monkeypatch.setattr(oidc_auth.httpx, "AsyncClient", _FakeAsyncClient)

    discovery = {"jwks_uri": "https://accounts.google.com/jwks", "issuer": issuer}
    claims = await oidc_auth._decode_id_token(discovery, id_token, "nonce123", access_token)
    assert claims["sub"] == "google-sub-123"
    assert claims["email"] == "user@gmail.com"


@pytest.mark.asyncio
async def test_oidc_config_disabled_by_default(client: AsyncClient, clean_db):
    response = await client.get("/api/auth/oidc/config")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["local_auth_enabled"] is True


@pytest.mark.asyncio
async def test_oidc_config_exposes_local_auth_disabled(client: AsyncClient, clean_db, oidc_settings):
    oidc_settings.local_auth_enabled = False

    response = await client.get("/api/auth/oidc/config")

    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["provider_name"] == "Pocket ID"
    assert data["local_auth_enabled"] is False


@pytest.mark.asyncio
async def test_oidc_config_never_reaches_the_provider(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    """The sign-in page blocks on /config; it must not wait on the provider."""

    async def fail_discover():
        raise AssertionError("/config must not read the discovery document")

    monkeypatch.setattr(oidc_auth, "_discover", fail_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)

    response = await client.get("/api/auth/oidc/config")

    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert "logout_url" not in data


@pytest.mark.asyncio
async def test_oidc_logout_url_comes_from_the_discovery_document(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    """Signing out must be able to end the provider's session too (#1623)."""

    async def fake_discover():
        return {"end_session_endpoint": "https://id.example.com/logout"}

    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)

    response = await client.get("/api/auth/oidc/logout-url")

    assert response.status_code == 200
    assert response.json()["logout_url"] == "https://id.example.com/logout?client_id=securo"


@pytest.mark.asyncio
async def test_oidc_logout_url_prefers_the_configured_one(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    oidc_settings.oidc_logout_url = "https://sso.example.com/logout?client_id=other"

    async def fail_discover():
        raise AssertionError("an explicit OIDC_LOGOUT_URL must not need discovery")

    monkeypatch.setattr(oidc_auth, "_discover", fail_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)

    response = await client.get("/api/auth/oidc/logout-url")

    assert response.status_code == 200
    assert response.json()["logout_url"] == "https://sso.example.com/logout?client_id=other"


@pytest.mark.asyncio
async def test_oidc_logout_url_keeps_a_return_address_put_on_it(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    """An operator sends people back after provider sign-out by putting the
    registered return address in OIDC_LOGOUT_URL (#1639)."""
    oidc_settings.oidc_logout_url = (
        "https://sso.example.com/logout"
        "?post_logout_redirect_uri=https%3A%2F%2Fapp.example.com%2Flogin"
    )
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)

    response = await client.get("/api/auth/oidc/logout-url")

    assert response.json()["logout_url"] == (
        "https://sso.example.com/logout"
        "?post_logout_redirect_uri=https%3A%2F%2Fapp.example.com%2Flogin"
        "&client_id=securo"
    )


@pytest.mark.asyncio
async def test_oidc_logout_url_is_empty_when_the_provider_lacks_one(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    async def fake_discover():
        return {"authorization_endpoint": "https://id.example.com/authorize"}

    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)

    response = await client.get("/api/auth/oidc/logout-url")

    assert response.status_code == 200
    assert response.json()["logout_url"] == ""


@pytest.mark.asyncio
async def test_oidc_logout_url_keeps_working_when_discovery_is_down(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    """The local sign-out stands even when the provider is unreachable."""

    async def failing_discover():
        raise oidc_auth.httpx.ConnectError("provider down")

    monkeypatch.setattr(oidc_auth, "_discover", failing_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)

    response = await client.get("/api/auth/oidc/logout-url")

    assert response.status_code == 200
    assert response.json()["logout_url"] == ""


@pytest.mark.asyncio
async def test_oidc_logout_url_survives_a_malformed_configured_url(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    """A broken OIDC_LOGOUT_URL must not turn the sign-out into a 500 (#1623)."""
    oidc_settings.oidc_logout_url = "https://[broken"
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)

    response = await client.get("/api/auth/oidc/logout-url")

    assert response.status_code == 200
    assert response.json()["logout_url"] == ""


@pytest.mark.asyncio
async def test_oidc_logout_url_survives_a_malformed_end_session_endpoint(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    async def fake_discover():
        return {"end_session_endpoint": "https://[broken"}

    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)

    response = await client.get("/api/auth/oidc/logout-url")

    assert response.status_code == 200
    assert response.json()["logout_url"] == ""


@pytest.mark.asyncio
async def test_oidc_logout_url_ignores_a_discovery_document_that_is_not_an_object(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    async def fake_discover():
        return ["not", "a", "document"]

    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)

    response = await client.get("/api/auth/oidc/logout-url")

    assert response.status_code == 200
    assert response.json()["logout_url"] == ""


@pytest.mark.asyncio
async def test_oidc_logout_url_gives_up_on_a_slow_discovery(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    """The browser waits on this answer; the discovery must not hold it."""
    import asyncio

    async def slow_discover():
        await asyncio.sleep(30)
        return {"end_session_endpoint": "https://id.example.com/logout"}

    monkeypatch.setattr(oidc_auth, "_discover", slow_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    monkeypatch.setattr(oidc_auth, "_LOGOUT_DISCOVERY_TIMEOUT", 0.05)

    response = await client.get("/api/auth/oidc/logout-url")

    assert response.status_code == 200
    assert response.json()["logout_url"] == ""


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_oidc_logout_url_retries_the_discovery_soon_after_a_failure(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    """One discovery hiccup must not disable provider sign-out for minutes
    (#1639): a failed lookup is remembered for seconds, not the long TTL."""
    clock = FakeClock()
    calls = []

    async def flaky_discover():
        calls.append(clock.now)
        if len(calls) == 1:
            raise oidc_auth.httpx.ConnectError("provider hiccup")
        return {"end_session_endpoint": "https://id.example.com/logout"}

    monkeypatch.setattr(oidc_auth, "_discover", flaky_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    monkeypatch.setattr(oidc_auth.time, "monotonic", clock)

    response = await client.get("/api/auth/oidc/logout-url")
    assert response.json()["logout_url"] == ""
    assert len(calls) == 1

    clock.now += 10
    response = await client.get("/api/auth/oidc/logout-url")
    assert response.json()["logout_url"] == ""
    assert len(calls) == 1, "a burst of sign-outs must not hammer a provider that is down"

    clock.now += 31
    response = await client.get("/api/auth/oidc/logout-url")
    assert response.json()["logout_url"] == "https://id.example.com/logout?client_id=securo"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_oidc_logout_url_keeps_a_resolved_answer_cached(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    """The long TTL still applies when the discovery answers (#1639)."""
    clock = FakeClock()
    calls = []

    async def counting_discover():
        calls.append(clock.now)
        return {"end_session_endpoint": "https://id.example.com/logout"}

    monkeypatch.setattr(oidc_auth, "_discover", counting_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    monkeypatch.setattr(oidc_auth.time, "monotonic", clock)

    response = await client.get("/api/auth/oidc/logout-url")
    assert response.json()["logout_url"] == "https://id.example.com/logout?client_id=securo"

    clock.now += oidc_auth._LOGOUT_URL_TTL - 1
    response = await client.get("/api/auth/oidc/logout-url")
    assert response.json()["logout_url"] == "https://id.example.com/logout?client_id=securo"
    assert len(calls) == 1

    clock.now += 2
    response = await client.get("/api/auth/oidc/logout-url")
    assert response.json()["logout_url"] == "https://id.example.com/logout?client_id=securo"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_oidc_logout_url_does_not_retry_a_provider_without_logout_often(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    """A discovery document with no end_session_endpoint is a valid answer, so
    it keeps the long TTL instead of being retried like a failure (#1639)."""
    clock = FakeClock()
    calls = []

    async def counting_discover():
        calls.append(clock.now)
        return {"authorization_endpoint": "https://id.example.com/authorize"}

    monkeypatch.setattr(oidc_auth, "_discover", counting_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    monkeypatch.setattr(oidc_auth.time, "monotonic", clock)

    response = await client.get("/api/auth/oidc/logout-url")
    assert response.json()["logout_url"] == ""

    clock.now += 60
    response = await client.get("/api/auth/oidc/logout-url")
    assert response.json()["logout_url"] == ""
    assert len(calls) == 1


def _oidc_warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "app.api.oidc_auth"]


@pytest.mark.asyncio
async def test_logout_discovery_failure_logs_once_per_cache_window(
    oidc_settings, monkeypatch, caplog
):
    """A failing discovery turns SSO sign-out off; `/config` is public, so the
    warning is written once, where the failure is cached."""
    calls = 0

    async def failing_discover():
        nonlocal calls
        calls += 1
        raise oidc_auth.httpx.ConnectError("provider down")

    monkeypatch.setattr(oidc_auth, "_discover", failing_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    caplog.set_level(logging.WARNING, logger="app.api.oidc_auth")

    assert await oidc_auth._provider_logout_url() == ""

    warnings = _oidc_warnings(caplog)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert oidc_settings.oidc_discovery_url in message
    assert "provider down" in message

    caplog.clear()

    # Inside the window: same empty answer, no second line, no second read.
    assert await oidc_auth._provider_logout_url() == ""
    assert _oidc_warnings(caplog) == []
    assert calls == 1


@pytest.mark.asyncio
async def test_logout_discovery_warning_hides_url_credentials(
    oidc_settings, monkeypatch, caplog
):
    """A credential in the discovery URL's userinfo or query never reaches the
    log, not even quoted back by the httpx error; discovery still reads the
    configured URL as it is."""
    secret_url = (
        "https://user:hunter2@id.example.com/.well-known/openid-configuration"
        "?token=s3cr3t-token"
    )
    oidc_settings.oidc_discovery_url = secret_url
    seen: list[str] = []

    async def failing_discover():
        seen.append(oidc_auth.get_settings().oidc_discovery_url)
        raise oidc_auth.httpx.ConnectError(f"cannot reach {secret_url}")

    monkeypatch.setattr(oidc_auth, "_discover", failing_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    caplog.set_level(logging.WARNING, logger="app.api.oidc_auth")

    assert await oidc_auth._provider_logout_url() == ""

    assert seen == [secret_url]
    warnings = _oidc_warnings(caplog)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "https://id.example.com/.well-known/openid-configuration" in message
    assert "hunter2" not in message
    assert "user:" not in message
    assert "s3cr3t-token" not in message


@pytest.mark.asyncio
async def test_logout_discovery_survives_an_unparsable_discovery_url(
    oidc_settings, monkeypatch, caplog
):
    """httpx.InvalidURL is neither an HTTPError nor a ValueError; it still
    answers "" and is reported like any other discovery failure."""
    oidc_settings.oidc_discovery_url = "https://[::1/.well-known/openid-configuration"
    monkeypatch.setattr(oidc_auth, "_discover", _real_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    caplog.set_level(logging.WARNING, logger="app.api.oidc_auth")

    assert await oidc_auth._provider_logout_url() == ""

    warnings = _oidc_warnings(caplog)
    assert len(warnings) == 1
    assert "InvalidURL" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_logout_discovery_survives_an_out_of_range_port(
    oidc_settings, monkeypatch, caplog
):
    """On Linux a port such as :99999 fails inside the connect as an
    ExceptionGroup wrapping OverflowError."""

    async def failing_discover():
        raise ExceptionGroup(
            "connection failed", [OverflowError("port must be 0-65535.")]
        )

    monkeypatch.setattr(oidc_auth, "_discover", failing_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    caplog.set_level(logging.WARNING, logger="app.api.oidc_auth")

    assert await oidc_auth._provider_logout_url() == ""

    assert len(_oidc_warnings(caplog)) == 1


def test_loggable_url_keeps_port_and_handles_broken_urls():
    assert (
        oidc_auth._loggable_url("http://u:p@idp.local:8443/realms/x?k=v#frag")
        == "http://idp.local:8443/realms/x"
    )
    assert oidc_auth._loggable_url("https://[broken") == "<unparsable discovery URL>"


@pytest.mark.asyncio
async def test_logout_discovery_timeout_is_reported(oidc_settings, monkeypatch, caplog):
    async def slow_discover():
        await asyncio.sleep(30)
        return {"end_session_endpoint": "https://id.example.com/logout"}

    monkeypatch.setattr(oidc_auth, "_discover", slow_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    monkeypatch.setattr(oidc_auth, "_LOGOUT_DISCOVERY_TIMEOUT", 0.05)
    caplog.set_level(logging.WARNING, logger="app.api.oidc_auth")

    assert await oidc_auth._provider_logout_url() == ""

    warnings = _oidc_warnings(caplog)
    assert len(warnings) == 1
    assert "timed out" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_logout_discovery_http_error_is_reported(oidc_settings, monkeypatch, caplog):
    request = oidc_auth.httpx.Request("GET", oidc_settings.oidc_discovery_url)
    response = oidc_auth.httpx.Response(503, request=request)

    async def failing_discover():
        raise oidc_auth.httpx.HTTPStatusError(
            "service unavailable", request=request, response=response
        )

    monkeypatch.setattr(oidc_auth, "_discover", failing_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    caplog.set_level(logging.WARNING, logger="app.api.oidc_auth")

    assert await oidc_auth._provider_logout_url() == ""

    warnings = _oidc_warnings(caplog)
    assert len(warnings) == 1
    assert "HTTP 503" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_logout_discovery_bad_end_session_url_is_reported(
    oidc_settings, monkeypatch, caplog
):
    async def fake_discover():
        return {"end_session_endpoint": "https://[broken"}

    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    caplog.set_level(logging.WARNING, logger="app.api.oidc_auth")

    assert await oidc_auth._provider_logout_url() == ""

    warnings = _oidc_warnings(caplog)
    assert len(warnings) == 1
    assert "not a usable URL" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_successful_logout_discovery_logs_nothing(oidc_settings, monkeypatch, caplog):
    async def fake_discover():
        return {"end_session_endpoint": "https://id.example.com/logout"}

    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_logout_url_cache", None)
    caplog.set_level(logging.WARNING, logger="app.api.oidc_auth")

    url = await oidc_auth._provider_logout_url()

    assert url == "https://id.example.com/logout?client_id=securo"
    assert _oidc_warnings(caplog) == []


@pytest.mark.asyncio
async def test_oidc_logout_url_is_empty_when_oidc_is_off(client: AsyncClient, clean_db):
    response = await client.get("/api/auth/oidc/logout-url")

    assert response.status_code == 200
    assert response.json()["logout_url"] == ""


@pytest.mark.asyncio
async def test_oidc_login_redirects_to_provider(client: AsyncClient, clean_db, oidc_settings, monkeypatch):
    fake_redis = FakeRedis()

    async def fake_discover():
        return {"authorization_endpoint": "https://id.example.com/authorize"}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)

    response = await client.get("/api/auth/oidc/login", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "id.example.com"
    assert parsed.path == "/authorize"
    assert params["client_id"] == ["securo"]
    assert params["scope"] == ["openid email profile"]
    assert params["redirect_uri"] == ["http://test/api/auth/oidc/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]
    stored = json.loads(fake_redis.store[f"oidc_state:{params['state'][0]}"])
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(stored["code_verifier"].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert params["code_challenge"][0] == expected_challenge


@pytest.mark.asyncio
async def test_oidc_callback_creates_user_and_redirects_with_securo_token(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        assert code == "abc"
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        assert id_token == "id-token"
        assert nonce == "nonce123"
        return {"sub": "user-sub", "email": "oidc@example.com", "email_verified": True, "name": "OIDC User"}

    async def fake_userinfo(discovery, access_token):
        assert access_token == "provider-token"
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("http://test/auth/oidc/callback#access_token=")

    token = parse_qs(urlparse(location).fragment)["access_token"][0]
    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "oidc@example.com"


@pytest.mark.asyncio
async def test_oidc_callback_syncs_existing_user_admin_and_workspace_role(
    client: AsyncClient, session, test_user, oidc_settings, monkeypatch
):
    oidc_settings.oidc_sync_roles = True
    oidc_settings.oidc_admin_roles = "securo-admins"
    test_user.oidc_issuer = "https://id.example.com"
    test_user.oidc_subject = "user-sub"
    session.add(test_user)
    await session.commit()
    oidc_settings.oidc_workspace_role_map = json.dumps(
        {
            "securo-viewers": "viewer",
            "securo-editors": "editor",
            "securo-owners": "owner",
        }
    )
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {
            "sub": "user-sub",
            "email": "test@example.com",
            "email_verified": True,
            "groups": ["securo-admins", "securo-editors"],
        }

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)
    assert response.status_code == 307
    token = parse_qs(urlparse(response.headers["location"]).fragment)["access_token"][0]

    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["is_superuser"] is True

    workspace = await client.get("/api/workspaces/current", headers={"Authorization": f"Bearer {token}"})
    assert workspace.status_code == 200
    assert workspace.json()["role"] == "owner"

    member = (
        await session.execute(select(WorkspaceMember).where(WorkspaceMember.user_id == test_user.id))
    ).scalars().first()
    assert member.role == "owner"


@pytest.mark.asyncio
async def test_oidc_callback_sync_roles_can_revoke_admin(
    client: AsyncClient, session, test_superuser, oidc_settings, monkeypatch
):
    oidc_settings.oidc_sync_roles = True
    oidc_settings.oidc_admin_roles = "securo-admins"
    test_superuser.oidc_issuer = "https://id.example.com"
    test_superuser.oidc_subject = "user-sub"
    session.add(test_superuser)
    await session.commit()
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {
            "sub": "user-sub",
            "email": "admin@example.com",
            "email_verified": True,
            "groups": ["securo-users"],
        }

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)
    assert response.status_code == 307
    token = parse_qs(urlparse(response.headers["location"]).fragment)["access_token"][0]

    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["is_superuser"] is False


@pytest.mark.asyncio
async def test_oidc_callback_signed_claims_override_userinfo_roles(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    oidc_settings.oidc_sync_roles = True
    oidc_settings.oidc_admin_roles = "securo-admins"
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {
            "sub": "signed-sub",
            "email": "userinfo-override@example.com",
            "email_verified": True,
            "groups": ["securo-users"],
        }

    async def fake_userinfo(discovery, access_token):
        return {
            "sub": "signed-sub",
            "email": "attacker@example.com",
            "email_verified": True,
            "groups": ["securo-admins"],
        }

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)
    assert response.status_code == 307
    token = parse_qs(urlparse(response.headers["location"]).fragment)["access_token"][0]

    me = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "userinfo-override@example.com"
    assert me.json()["is_superuser"] is False


@pytest.mark.asyncio
async def test_oidc_callback_rejects_userinfo_subject_mismatch(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "signed-sub", "email": "oidc@example.com", "email_verified": True}

    async def fake_userinfo(discovery, access_token):
        return {"sub": "different-sub", "email": "oidc@example.com", "email_verified": True}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123")
    assert response.status_code == 400
    assert response.json()["detail"] == "OIDC userinfo subject does not match id_token subject"


@pytest.mark.asyncio
async def test_oidc_callback_requires_verified_email_claim_when_enabled(
    client: AsyncClient, clean_db, oidc_settings, monkeypatch
):
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "signed-sub", "email": "oidc@example.com"}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123")
    assert response.status_code == 400
    assert response.json()["detail"] == "OIDC email is not verified"


@pytest.mark.asyncio
async def test_oidc_callback_rejects_unlinked_existing_user_by_default(
    client: AsyncClient, test_user, oidc_settings, monkeypatch
):
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "new-sub", "email": "test@example.com", "email_verified": True}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123")

    assert response.status_code == 403
    assert response.json()["detail"] == "Existing account is not linked to this OIDC identity"


@pytest.mark.asyncio
async def test_oidc_callback_verified_email_link_mode_links_existing_user(
    client: AsyncClient, session, test_user, oidc_settings, monkeypatch
):
    oidc_settings.oidc_existing_user_link_mode = "verified_email"
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "linked-sub", "email": "test@example.com", "email_verified": True}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)

    assert response.status_code == 307
    await session.refresh(test_user)
    assert test_user.oidc_issuer == "https://id.example.com"
    assert test_user.oidc_subject == "linked-sub"


PAIR_ADDRESS = "pair@example.com"
PAIR_OTHER_CASE = "Pair@example.com"


async def _pair_of_accounts(session) -> tuple[User, User]:
    """Two accounts for one mailbox in different case, as an older release
    left them: the column's unique index is case-sensitive (#568).

    The row written in the lower-case spelling is given the higher id, so an
    ordering by id alone would choose the other one: only the exact spelling
    can make the lower-case row the answer.
    """
    import uuid as _uuid

    pair = []
    for address, account_id in (
        (PAIR_ADDRESS, _uuid.UUID(int=2)),
        (PAIR_OTHER_CASE, _uuid.UUID(int=1)),
    ):
        user = User(
            id=account_id,
            email=address,
            hashed_password="synthetic-unused",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        pair.append(user)
    await session.commit()
    for user in pair:
        await session.refresh(user)
    return pair[0], pair[1]


def _fake_provider(monkeypatch, *, email: str, subject: str = "shared-sub") -> FakeRedis:
    """The provider answering with one identity, without the network."""
    fake_redis = FakeRedis()

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": subject, "email": email, "email_verified": True}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)
    return fake_redis


@pytest.mark.parametrize(
    ("link_mode", "expected_status"),
    [("disabled", 403), ("verified_email", 307), ("email", 307)],
)
@pytest.mark.parametrize("provider_email", [PAIR_ADDRESS, PAIR_OTHER_CASE])
@pytest.mark.asyncio
async def test_oidc_callback_resolves_an_address_shared_by_two_accounts(
    client: AsyncClient,
    session,
    clean_db,
    oidc_settings,
    monkeypatch,
    link_mode,
    expected_status,
    provider_email,
):
    """One address naming two accounts answers like any other account: never
    a 500, in any of the three link modes, and the row written in the exact
    spelling the provider sent is the one that gets linked (#568)."""
    oidc_settings.oidc_existing_user_link_mode = link_mode
    lower_row, upper_row = await _pair_of_accounts(session)
    spelled = lower_row if provider_email == PAIR_ADDRESS else upper_row
    other = upper_row if provider_email == PAIR_ADDRESS else lower_row
    fake_redis = _fake_provider(monkeypatch, email=provider_email)
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    response = await client.get(
        "/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False
    )

    assert response.status_code == expected_status, response.text
    await session.refresh(spelled)
    await session.refresh(other)
    if expected_status == 307:
        assert spelled.oidc_issuer == "https://id.example.com"
        assert spelled.oidc_subject == "shared-sub"
    else:
        assert spelled.oidc_issuer is None
    # The other spelling is left alone in every mode.
    assert other.oidc_issuer is None
    assert other.oidc_subject is None


@pytest.mark.asyncio
async def test_oidc_auto_registration_stores_the_address_in_lower_case(
    client: AsyncClient, session, clean_db, oidc_settings, monkeypatch
):
    """The spelling the provider sent is only used to find an existing row:
    an account created here is stored in lower case, so the new row never
    arrives shouting (#568)."""
    fake_redis = _fake_provider(monkeypatch, email=PAIR_OTHER_CASE, subject="new-sub")
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    response = await client.get(
        "/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False
    )

    assert response.status_code == 307, response.text
    registered = (
        await session.execute(select(User).where(User.oidc_subject == "new-sub"))
    ).scalar_one()
    assert registered.email == PAIR_ADDRESS


@pytest.mark.asyncio
async def test_oidc_callback_rejects_existing_user_with_different_linked_subject(
    client: AsyncClient, session, test_user, oidc_settings, monkeypatch
):
    oidc_settings.oidc_existing_user_link_mode = "verified_email"
    test_user.oidc_issuer = "https://id.example.com"
    test_user.oidc_subject = "original-sub"
    session.add(test_user)
    await session.commit()
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "different-sub", "email": "test@example.com", "email_verified": True}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123")

    assert response.status_code == 403
    assert response.json()["detail"] == "OIDC identity is linked to a different account"


@pytest.mark.asyncio
async def test_oidc_callback_respects_disabled_registration_setting(
    client: AsyncClient, session, clean_db, oidc_settings, monkeypatch
):
    oidc_settings.registration_enabled = True
    oidc_settings.oidc_auto_register = True
    session.add(AppSetting(key="registration_enabled", value="false"))
    await session.commit()
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "new-sub", "email": "new@example.com", "email_verified": True}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123")

    assert response.status_code == 403
    assert response.json()["detail"] == "Registration is disabled"


@pytest.mark.asyncio
async def test_oidc_callback_email_link_mode_links_existing_user_without_verified_email(
    client: AsyncClient, session, test_user, oidc_settings, monkeypatch
):
    oidc_settings.oidc_existing_user_link_mode = "email"
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "unverified-linked-sub", "email": "test@example.com"}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)

    assert response.status_code == 307
    await session.refresh(test_user)
    assert test_user.oidc_issuer == "https://id.example.com"
    assert test_user.oidc_subject == "unverified-linked-sub"


@pytest.mark.asyncio
async def test_oidc_callback_linked_user_can_login_without_verified_email_when_required(
    client: AsyncClient, session, test_user, oidc_settings, monkeypatch
):
    oidc_settings.oidc_require_verified_email = True
    oidc_settings.oidc_existing_user_link_mode = "verified_email"
    test_user.oidc_issuer = "https://id.example.com"
    test_user.oidc_subject = "linked-sub"
    session.add(test_user)
    await session.commit()
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "linked-sub", "email": "test@example.com"}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)

    assert response.status_code == 307


@pytest.mark.asyncio
async def test_oidc_callback_linked_user_can_login_without_email_claim(
    client: AsyncClient, session, test_user, oidc_settings, monkeypatch
):
    oidc_settings.oidc_require_verified_email = True
    test_user.oidc_issuer = "https://id.example.com"
    test_user.oidc_subject = "linked-sub"
    session.add(test_user)
    await session.commit()
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "linked-sub"}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)

    assert response.status_code == 307


@pytest.mark.asyncio
async def test_oidc_callback_registration_disabled_does_not_block_existing_email_link(
    client: AsyncClient, session, test_user, oidc_settings, monkeypatch
):
    oidc_settings.oidc_existing_user_link_mode = "email"
    session.add(AppSetting(key="registration_enabled", value="false"))
    await session.commit()
    fake_redis = FakeRedis()
    await fake_redis.set("oidc_state:state123", json.dumps({"nonce": "nonce123"}))

    async def fake_discover():
        return {"issuer": "https://id.example.com"}

    async def fake_exchange(discovery, code, code_verifier=""):
        return {"id_token": "id-token", "access_token": "provider-token"}

    async def fake_decode(discovery, id_token, nonce, access_token=""):
        return {"sub": "linked-with-registration-disabled", "email": "test@example.com"}

    async def fake_userinfo(discovery, access_token):
        return {}

    async def fake_get_redis():
        return fake_redis

    monkeypatch.setattr(oidc_auth, "get_redis", fake_get_redis)
    monkeypatch.setattr(oidc_auth, "_discover", fake_discover)
    monkeypatch.setattr(oidc_auth, "_exchange_code", fake_exchange)
    monkeypatch.setattr(oidc_auth, "_decode_id_token", fake_decode)
    monkeypatch.setattr(oidc_auth, "_fetch_userinfo", fake_userinfo)

    response = await client.get("/api/auth/oidc/callback?code=abc&state=state123", follow_redirects=False)

    assert response.status_code == 307
    await session.refresh(test_user)
    assert test_user.oidc_subject == "linked-with-registration-disabled"


async def _account_created_by_the_racing_request(
    session: AsyncSession,
    *,
    email: str = "race@example.com",
    oidc_issuer: str | None = None,
    oidc_subject: str | None = None,
    is_verified: bool = True,
) -> User:
    """The account a second request commits between our lookup and our insert,
    provisioned the way a completed sign-up leaves it."""
    user = User(
        email=email,
        hashed_password="not-used-by-these-tests",
        is_active=True,
        is_superuser=False,
        is_verified=is_verified,
        preferences={
            "language": "pt-BR",
            "date_format": "DD/MM/YYYY",
            "timezone": "America/Sao_Paulo",
            "currency_display": "BRL",
            "display_name": "The first one to arrive",
        },
        oidc_issuer=oidc_issuer,
        oidc_subject=oidc_subject,
    )
    session.add(user)
    await session.flush()
    workspace = await create_personal_workspace_for_user(session, user)
    session.add(
        Account(
            user_id=user.id,
            workspace_id=workspace.id,
            name="Carteira",
            type="checking",
            balance=0,
            currency="BRL",
        )
    )
    session.add(
        Rule(
            user_id=user.id,
            workspace_id=workspace.id,
            name="Racing rule",
            conditions_op="and",
            conditions=[],
            actions=[],
            priority=0,
        )
    )
    await session.commit()
    await session.refresh(user)
    return user


def _lose_the_race_to_a_second_request(monkeypatch, create_winner) -> dict:
    """Have the account committed just before the manager's own insert, so the
    create raises `UserAlreadyExists` exactly like a concurrent sign-up."""
    state = {"won": False}
    original_create = UserManager.create

    async def create_after_the_race(self, user_create, safe=False, request=None):
        if not state["won"]:
            state["won"] = True
            await create_winner()
        return await original_create(self, user_create, safe=safe, request=request)

    monkeypatch.setattr(UserManager, "create", create_after_the_race)
    return state


async def _run_oidc_login(
    session: AsyncSession,
    *,
    subject: str = "race-sub",
    email: str = "race@example.com",
    email_verified: bool = True,
    issuer: str = "https://id.example.com",
):
    manager = UserManager(SQLAlchemyUserDatabase(session, User))
    return await oidc_auth._get_or_create_oidc_user(
        {"sub": subject, "email": email, "email_verified": email_verified},
        {},
        issuer,
        session,
        manager,
    )


async def _count_for(session: AsyncSession, column, user_id) -> int:
    return (
        await session.execute(select(func.count()).select_from(column).where(column.user_id == user_id))
    ).scalar_one()


@pytest.mark.asyncio
async def test_oidc_registration_race_links_the_account_that_won(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """A concurrent sign-up that creates the account between our lookup and our
    insert is an existing account like any other: the identity is linked to it,
    its preferences stay, and it gets no second wallet or default rules.
    """
    oidc_settings.oidc_existing_user_link_mode = "verified_email"
    raced: list[User] = []

    async def create_winner():
        raced.append(await _account_created_by_the_racing_request(session))

    state = _lose_the_race_to_a_second_request(monkeypatch, create_winner)

    user = await _run_oidc_login(session)

    assert state["won"] is True
    winner = raced[0]
    assert user.id == winner.id
    await session.refresh(winner)
    assert winner.oidc_issuer == "https://id.example.com"
    assert winner.oidc_subject == "race-sub"
    preferences = winner.preferences or {}
    assert preferences["language"] == "pt-BR"
    assert preferences["display_name"] == "The first one to arrive"
    assert await _count_for(session, WorkspaceMember, winner.id) == 1
    assert await _count_for(session, Account, winner.id) == 1
    assert await _count_for(session, Rule, winner.id) == 1


@pytest.mark.asyncio
async def test_oidc_registration_race_respects_the_link_mode(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """The account that won the race is not claimed by an unexpected OIDC
    identity when the link mode does not allow it."""
    oidc_settings.oidc_existing_user_link_mode = "disabled"
    raced: list[User] = []

    async def create_winner():
        raced.append(await _account_created_by_the_racing_request(session))

    _lose_the_race_to_a_second_request(monkeypatch, create_winner)

    with pytest.raises(HTTPException) as refused:
        await _run_oidc_login(session)

    assert refused.value.status_code == 403
    assert refused.value.detail == "Existing account is not linked to this OIDC identity"
    winner = raced[0]
    await session.refresh(winner)
    assert winner.oidc_issuer is None
    assert winner.oidc_subject is None
    preferences = winner.preferences or {}
    assert preferences["language"] == "pt-BR"
    assert await _count_for(session, Account, winner.id) == 1
    assert await _count_for(session, Rule, winner.id) == 1


@pytest.mark.asyncio
async def test_oidc_registration_race_returns_the_account_of_the_same_identity(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """When the second request is this same identity, the account is a login,
    not a new link: it answers as it is and nothing of it is provisioned again."""
    oidc_settings.oidc_existing_user_link_mode = "disabled"
    raced: list[User] = []

    async def create_winner():
        raced.append(
            await _account_created_by_the_racing_request(
                session,
                oidc_issuer="https://id.example.com",
                oidc_subject="race-sub",
            )
        )

    _lose_the_race_to_a_second_request(monkeypatch, create_winner)

    user = await _run_oidc_login(session)

    winner = raced[0]
    assert user.id == winner.id
    await session.refresh(winner)
    preferences = winner.preferences or {}
    assert preferences["language"] == "pt-BR"
    assert await _count_for(session, Account, winner.id) == 1
    assert await _count_for(session, Rule, winner.id) == 1


@pytest.mark.asyncio
async def test_oidc_registration_race_refuses_an_account_linked_elsewhere(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """An account the second request already linked to another OIDC identity
    keeps that identity: the loser does not overwrite it."""
    oidc_settings.oidc_existing_user_link_mode = "email"
    raced: list[User] = []

    async def create_winner():
        raced.append(
            await _account_created_by_the_racing_request(
                session,
                oidc_issuer="https://id.example.com",
                oidc_subject="another-sub",
            )
        )

    _lose_the_race_to_a_second_request(monkeypatch, create_winner)

    with pytest.raises(HTTPException) as refused:
        await _run_oidc_login(session)

    assert refused.value.status_code == 403
    assert refused.value.detail == "OIDC identity is linked to a different account"
    winner = raced[0]
    await session.refresh(winner)
    assert winner.oidc_subject == "another-sub"
    assert await _count_for(session, Account, winner.id) == 1


@pytest.mark.asyncio
async def test_oidc_registration_writes_the_identity_with_the_account(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """The row comes out of the insert with its identity and preferences.

    A second sign-in can read that row at any moment after the insert, so a row
    that only gets its identity in a later update is an unlinked account for
    that request: with the default link mode it is refused, and with a linking
    mode it is claimed. Read right after the insert, before the sign-up goes on
    to provision anything, the identity is already there.
    """
    oidc_settings.oidc_existing_user_link_mode = "disabled"
    inserted: list[dict] = []
    original_create = UserManager.create

    async def inspect_after_the_insert(self, user_create, safe=False, request=None):
        user = await original_create(self, user_create, safe=safe, request=request)
        inserted.append(
            {
                "oidc_issuer": user.oidc_issuer,
                "oidc_subject": user.oidc_subject,
                "preferences": dict(user.preferences or {}),
            }
        )
        return user

    monkeypatch.setattr(UserManager, "create", inspect_after_the_insert)

    await _run_oidc_login(session)

    assert inserted == [
        {
            "oidc_issuer": "https://id.example.com",
            "oidc_subject": "race-sub",
            "preferences": {
                "language": "en",
                "date_format": "MM/DD/YYYY",
                "timezone": "UTC",
                "currency_display": get_settings().default_currency,
                "display_name": "race",
                "auth_provider": "Pocket ID",
            },
        }
    ]


@pytest.mark.asyncio
async def test_oidc_registration_race_refuses_an_identity_from_another_issuer(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """The same subject under another issuer is another identity.

    Comparing only the subject would take the account of whoever holds that
    subject on the other provider.
    """
    oidc_settings.oidc_existing_user_link_mode = "email"
    raced: list[User] = []

    async def create_winner():
        raced.append(
            await _account_created_by_the_racing_request(
                session,
                oidc_issuer="https://other.example.com",
                oidc_subject="race-sub",
            )
        )

    _lose_the_race_to_a_second_request(monkeypatch, create_winner)

    with pytest.raises(HTTPException) as refused:
        await _run_oidc_login(session)

    assert refused.value.status_code == 403
    assert refused.value.detail == "OIDC identity is linked to a different account"
    winner = raced[0]
    await session.refresh(winner)
    assert winner.oidc_issuer == "https://other.example.com"
    assert await _count_for(session, Account, winner.id) == 1


@pytest.mark.asyncio
async def test_oidc_registration_race_refuses_an_unverified_address(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """In verified_email mode an unverified claim does not link, even to an
    account the race left unverified too: the claim is the only proof.

    The account-wide requirement stays off so the sign-in reaches the race:
    what refuses it is the link mode, on the account the other request left.
    """
    oidc_settings.oidc_existing_user_link_mode = "verified_email"
    oidc_settings.oidc_require_verified_email = False
    raced: list[User] = []

    async def create_winner():
        raced.append(await _account_created_by_the_racing_request(session, is_verified=False))

    _lose_the_race_to_a_second_request(monkeypatch, create_winner)

    with pytest.raises(HTTPException) as refused:
        await _run_oidc_login(session, email_verified=False)

    assert refused.value.status_code == 400
    assert refused.value.detail == "OIDC email is not verified"
    winner = raced[0]
    await session.refresh(winner)
    assert winner.oidc_issuer is None
    assert await _count_for(session, Account, winner.id) == 1


@pytest.mark.asyncio
async def test_oidc_registration_race_does_not_trust_a_verified_account(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """An account that is verified on its own row does not stand in for the
    claim: our unverified sign-in still does not link in verified_email mode."""
    oidc_settings.oidc_existing_user_link_mode = "verified_email"
    oidc_settings.oidc_require_verified_email = False
    raced: list[User] = []

    async def create_winner():
        raced.append(await _account_created_by_the_racing_request(session, is_verified=True))

    _lose_the_race_to_a_second_request(monkeypatch, create_winner)

    with pytest.raises(HTTPException) as refused:
        await _run_oidc_login(session, email_verified=False)

    assert refused.value.status_code == 400
    assert refused.value.detail == "OIDC email is not verified"
    winner = raced[0]
    await session.refresh(winner)
    assert winner.oidc_issuer is None
    assert await _count_for(session, Account, winner.id) == 1


@pytest.mark.asyncio
async def test_oidc_registration_race_survives_the_insert_colliding(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """When the winner commits after the manager's own lookup, the insert itself
    hits the unique address and raises IntegrityError. That collision is the
    same race, so it must answer like the UserAlreadyExists branch instead of
    500: a failed insert leaves the session unusable until it is rolled back."""
    oidc_settings.oidc_existing_user_link_mode = "verified_email"
    raced: list[User] = []
    state = {"won": False}
    original_create = SQLAlchemyUserDatabase.create

    async def create_after_the_lookup(self, create_dict):
        if not state["won"]:
            state["won"] = True
            raced.append(await _account_created_by_the_racing_request(session))
        return await original_create(self, create_dict)

    monkeypatch.setattr(SQLAlchemyUserDatabase, "create", create_after_the_lookup)

    user = await _run_oidc_login(session)

    assert state["won"] is True
    winner = raced[0]
    assert user.id == winner.id
    await session.refresh(winner)
    assert winner.oidc_issuer == "https://id.example.com"
    assert winner.oidc_subject == "race-sub"
    preferences = winner.preferences or {}
    assert preferences["language"] == "pt-BR"
    assert await _count_for(session, WorkspaceMember, winner.id) == 1
    assert await _count_for(session, Account, winner.id) == 1


@pytest.mark.asyncio
async def test_oidc_registration_race_looks_up_the_identity_before_the_address(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """The row is looked up by the identity before the address.

    Our insert can collide with an address another request committed while the
    account that carries this identity has since moved to another address. The
    identity is the only thing that names the same account in both requests, so
    it answers first: the address lookup would hand the login to the other
    account and leave the identity's owner locked out of it.
    """
    oidc_settings.oidc_existing_user_link_mode = "email"
    raced: dict[str, User] = {}

    async def create_winner():
        raced["identity"] = await _account_created_by_the_racing_request(
            session,
            email="moved@example.org",
            oidc_issuer="https://id.example.com",
            oidc_subject="race-sub",
        )
        raced["address"] = await _account_created_by_the_racing_request(session)

    _lose_the_race_to_a_second_request(monkeypatch, create_winner)

    user = await _run_oidc_login(session)

    assert user.id == raced["identity"].id
    other = raced["address"]
    await session.refresh(other)
    assert other.oidc_issuer is None
    assert other.oidc_subject is None


@pytest.mark.asyncio
async def test_oidc_registration_race_finds_the_address_in_another_spelling(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """The address lookup after the collision is case-insensitive.

    The winner stored the address in lower case and the provider may send it
    shouting. A lookup that compares the stored value as it is finds nothing,
    re-raises the conflict and answers an error for an account that exists.
    """
    oidc_settings.oidc_existing_user_link_mode = "email"
    raced: list[User] = []

    async def create_winner():
        raced.append(await _account_created_by_the_racing_request(session))

    _lose_the_race_to_a_second_request(monkeypatch, create_winner)

    user = await _run_oidc_login(session, email="Race@Example.com")

    winner = raced[0]
    assert user.id == winner.id
    await session.refresh(winner)
    assert winner.email == "race@example.com"
    assert winner.oidc_issuer == "https://id.example.com"
    assert winner.oidc_subject == "race-sub"


@pytest.mark.asyncio
async def test_oidc_registration_race_raises_the_conflict_when_no_row_is_found(
    session: AsyncSession, clean_db, oidc_settings, monkeypatch
):
    """A collision with no row behind it stays the conflict it was.

    The rollback and the two lookups exist to find the row the winner wrote; a
    constraint the database reports with no row to answer for must not be
    turned into a link to nothing, so the original conflict surfaces.
    """
    oidc_settings.oidc_existing_user_link_mode = "verified_email"

    async def create_fails(self, user_create, safe=False, request=None):
        raise UserAlreadyExists()

    monkeypatch.setattr(UserManager, "create", create_fails)

    with pytest.raises(UserAlreadyExists):
        await _run_oidc_login(session)

    remaining = await session.execute(
        select(func.count()).select_from(User).where(User.email == "race@example.com")
    )
    assert remaining.scalar_one() == 0
