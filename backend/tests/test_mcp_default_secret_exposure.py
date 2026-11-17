"""Regression: a token signed with a public default must never be accepted.

Issue #1174. This file imports nothing new, so it can run against `main` and
show the exposure there: the MCP server served `accept` responses for a
token anyone can sign with a string published in this repository.

The suite runs with `AGENTS_MCP_JWT_SECRET` set to a test value (conftest);
these tests force the two public defaults a real deployment used to fall
back to, and check that a forged token is refused instead of reaching data.
A public default in the environment is ignored and the key is derived from
SECRET_KEY (#1173), so the forged signature simply doesn't verify.
"""
from __future__ import annotations

import time
import uuid

import httpx
import pytest
from jose import jwt

from app.agents.mcp.auth import JWT_ALGO, JWT_AUDIENCE, JWT_ISSUER

PUBLIC_DEFAULTS = ("change-me-in-production", "dev-mcp-secret-change-in-production")


def _forge(user_id, secret: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": str(user_id),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": now,
            "exp": now + 600,
            "ext": True,
        },
        secret,
        algorithm=JWT_ALGO,
    )


@pytest.fixture
def mcp_secret(monkeypatch):
    """Set the server's signing key to a chosen value and reload settings."""
    from app.agents import config

    def apply(secret: str):
        monkeypatch.setenv("AGENTS_MCP_JWT_SECRET", secret)
        config.get_agent_settings.cache_clear()

    yield apply

    monkeypatch.delenv("AGENTS_MCP_JWT_SECRET", raising=False)
    config.get_agent_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("public_default", PUBLIC_DEFAULTS)
async def test_tools_list_rejects_token_signed_with_public_default(mcp_secret, public_default):
    """Signature check alone used to pass: the server's key was public."""
    from mcp_server.main import app

    mcp_secret(public_default)
    forged = _forge(uuid.uuid4(), public_default)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as cli:
        response = await cli.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {forged}"},
        )

    assert response.status_code == 401
    assert "tools" not in response.text
    # Refused, with a message that tells the token's holder what to do.
    assert "no longer accepts" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_forged_token_with_public_default_cannot_read_transactions(
    mcp_secret, test_user, test_workspace, test_transactions, monkeypatch
):
    """The issue's PoC: a token anyone can sign must not reach a user's rows."""
    import mcp_server.main as mcp_main
    from tests.conftest import TestSessionLocal

    mcp_secret("change-me-in-production")
    forged = _forge(test_user.id, "change-me-in-production")
    monkeypatch.setattr(mcp_main, "async_session_maker", TestSessionLocal)

    transport = httpx.ASGITransport(app=mcp_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as cli:
        response = await cli.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_transactions", "arguments": {"limit": 5}},
            },
            headers={"Authorization": f"Bearer {forged}"},
        )

    assert response.status_code == 401
    assert "SALARIO" not in response.text
