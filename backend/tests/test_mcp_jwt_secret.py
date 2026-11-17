"""The MCP signing key must never be a value anyone can read from the repo.

Issue #1174: `AGENTS_MCP_JWT_SECRET` shipped with public defaults (in the
code, in both Compose files and in the Helm chart), so a token signed with
a known string was accepted and could act as any user whose UUID was known
(the user directory hands those out to every registered user).

These tests pin two things:
  - a known public default in the environment is ignored, and a public
    SECRET_KEY is replaced by the instance key, so neither ever signs
  - with no explicit secret, the key is derived from SECRET_KEY, and the
    backend that mints and the server that verifies agree on it

`test_mcp_default_secret_exposure.py` holds the end-to-end regression, kept
in a file that also runs against `main`.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from jose import jwt as jose_jwt

from app.agents.mcp.auth import JWT_ALGO, mint_token
from app.agents.mcp.keys import UnsafeMCPSecret, resolve_mcp_jwt_secret
from mcp_server.auth import verify_request
from starlette.requests import Request

PUBLIC_MCP_DEFAULT = "change-me-in-production"
PUBLIC_COMPOSE_DEFAULT = "dev-mcp-secret-change-in-production"
PUBLIC_APP_DEFAULT = "dev-secret-change-in-production"
STRONG_SECRET = "a" * 64


@pytest.fixture
def env(monkeypatch):
    """Set the two secrets (or unset them) and reload the cached settings."""
    from app.agents import config
    from app.core import config as core_config

    def apply(*, mcp_secret=None, app_secret=None):
        if mcp_secret is None:
            monkeypatch.delenv("AGENTS_MCP_JWT_SECRET", raising=False)
        else:
            monkeypatch.setenv("AGENTS_MCP_JWT_SECRET", mcp_secret)
        if app_secret is not None:
            monkeypatch.setenv("SECRET_KEY", app_secret)
        config.get_agent_settings.cache_clear()
        core_config.get_settings.cache_clear()

    yield apply

    monkeypatch.undo()
    config.get_agent_settings.cache_clear()
    core_config.get_settings.cache_clear()


def _req_with(token: str) -> Request:
    return Request(scope={"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})


@pytest.mark.parametrize("public_default", [PUBLIC_MCP_DEFAULT, PUBLIC_COMPOSE_DEFAULT])
def test_public_default_mcp_secret_is_ignored(env, public_default):
    """Old Compose files and Helm values still pass a public default. It is
    never used to sign: the key is derived from SECRET_KEY instead, so those
    installs keep starting (#1173)."""
    env(mcp_secret=None, app_secret=STRONG_SECRET)
    derived = resolve_mcp_jwt_secret()
    env(mcp_secret=public_default, app_secret=STRONG_SECRET)

    assert resolve_mcp_jwt_secret() == derived
    assert resolve_mcp_jwt_secret() != public_default


def test_public_default_secret_key_derives_from_the_instance_key(env, monkeypatch, tmp_path):
    """SECRET_KEY on a public default is replaced by the instance's own key
    (app.core.instance_secrets), so the derived MCP key isn't public either."""
    import hashlib
    import hmac

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'i.sqlite'}")
    env(mcp_secret=None, app_secret=PUBLIC_APP_DEFAULT)

    key = resolve_mcp_jwt_secret()

    public = hmac.new(PUBLIC_APP_DEFAULT.encode(), b"securo:mcp-jwt:v1", hashlib.sha256).hexdigest()
    assert key != public
    assert len(key) == 64


def test_mint_with_a_public_default_signs_with_the_derived_key(env):
    env(mcp_secret=PUBLIC_MCP_DEFAULT, app_secret=STRONG_SECRET)
    token = mint_token(user_id=uuid.uuid4())

    assert verify_request(_req_with(token)).user_id is not None
    forged = jose_jwt.encode(
        jose_jwt.get_unverified_claims(token), PUBLIC_MCP_DEFAULT, algorithm=JWT_ALGO
    )
    with pytest.raises(HTTPException) as refused:
        verify_request(_req_with(forged))
    assert refused.value.status_code == 401


def test_derives_a_stable_key_from_secret_key(env):
    env(mcp_secret=None, app_secret=STRONG_SECRET)
    first = resolve_mcp_jwt_secret()
    assert first == resolve_mcp_jwt_secret()
    assert first != STRONG_SECRET
    assert len(first) == 64


def test_derived_key_changes_with_secret_key(env):
    env(mcp_secret=None, app_secret=STRONG_SECRET)
    one = resolve_mcp_jwt_secret()
    env(mcp_secret=None, app_secret="b" * 64)
    assert resolve_mcp_jwt_secret() != one


def test_explicit_secret_wins_over_derivation(env):
    env(mcp_secret="explicit-secret-that-is-not-a-public-default", app_secret=STRONG_SECRET)
    assert resolve_mcp_jwt_secret() == "explicit-secret-that-is-not-a-public-default"


def test_derived_key_mints_and_verifies(env):
    """Zero-config installs keep working: both sides derive the same value."""
    env(mcp_secret=None, app_secret=STRONG_SECRET)
    token = mint_token(user_id=uuid.uuid4())
    assert verify_request(_req_with(token)).user_id is not None


@pytest.mark.asyncio
async def test_server_starts_with_a_public_default_in_the_environment(env):
    """The value is ignored, not fatal: an install updated without touching
    its Compose file keeps its MCP server."""
    from mcp_server.main import lifespan, app

    env(mcp_secret=PUBLIC_MCP_DEFAULT, app_secret=STRONG_SECRET)
    async with lifespan(app):
        pass


@pytest.mark.asyncio
async def test_server_refuses_to_start_with_a_short_secret_key(env):
    """Deriving from a SECRET_KEY under 32 characters is still refused."""
    from mcp_server.main import lifespan, app

    env(mcp_secret=None, app_secret="short-but-private")
    with pytest.raises(UnsafeMCPSecret):
        async with lifespan(app):
            pass
