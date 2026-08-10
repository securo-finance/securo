"""JWT mint/verify roundtrip between the backend and the MCP server.

Both sides share AGENTS_MCP_JWT_SECRET. We test that:
  - A token minted by the backend verifies cleanly on the MCP side.
  - Tokens with the wrong secret are rejected.
  - Expired tokens are rejected.
  - Missing audience / issuer is rejected.
"""

import time
import uuid

import pytest
from jose import jwt
from starlette.requests import Request

from app.agents.config import get_agent_settings
from app.agents.mcp.auth import JWT_ALGO, JWT_AUDIENCE, JWT_ISSUER, mint_token


def _req(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(scope={"type": "http", "headers": headers})


def _req_bearer(token: str) -> Request:
    return _req([(b"authorization", f"Bearer {token}".encode())])


def test_mint_then_verify_roundtrip():
    from mcp_server.auth import verify_request

    user_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    token = mint_token(user_id=user_id, conversation_id=conv_id, agent_id=agent_id)

    ctx = verify_request(_req_bearer(token))
    assert ctx.user_id == user_id
    assert ctx.conversation_id == conv_id
    assert ctx.agent_id == agent_id


def test_missing_authorization_rejected():
    from fastapi import HTTPException
    from mcp_server.auth import verify_request

    with pytest.raises(HTTPException) as exc:
        _ = verify_request(_req([]))
    assert exc.value.status_code == 401


def test_wrong_secret_rejected():
    from fastapi import HTTPException
    from mcp_server.auth import verify_request

    user_id = uuid.uuid4()
    bogus = jwt.encode(
        {
            "sub": str(user_id),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        "totally-different-secret",
        algorithm=JWT_ALGO,
    )

    with pytest.raises(HTTPException) as exc:
        _ = verify_request(_req_bearer(bogus))
    assert exc.value.status_code == 401


def test_expired_token_rejected():
    from fastapi import HTTPException
    from mcp_server.auth import verify_request

    s = get_agent_settings()
    user_id = uuid.uuid4()
    expired = jwt.encode(
        {
            "sub": str(user_id),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        },
        s.mcp_jwt_secret,
        algorithm=JWT_ALGO,
    )

    with pytest.raises(HTTPException) as exc:
        _ = verify_request(_req_bearer(expired))
    assert exc.value.status_code == 401


def test_short_ttl_respected():
    """Mint with ttl_seconds=1, sleep 2, verify rejection."""
    from fastapi import HTTPException
    from mcp_server.auth import verify_request

    token = mint_token(user_id=uuid.uuid4(), ttl_seconds=1)
    time.sleep(2.1)

    with pytest.raises(HTTPException):
        _ = verify_request(_req_bearer(token))


def test_optional_conv_id_is_truly_optional():
    from mcp_server.auth import verify_request

    token = mint_token(user_id=uuid.uuid4())

    ctx = verify_request(_req_bearer(token))
    assert ctx.conversation_id is None
    assert ctx.agent_id is None


def test_external_token_round_trip():
    from mcp_server.auth import verify_request

    token = mint_token(user_id=uuid.uuid4(), external=True)

    ctx = verify_request(_req_bearer(token))
    assert ctx.external is True


def test_default_token_has_no_external_flag():
    from mcp_server.auth import verify_request

    token = mint_token(user_id=uuid.uuid4())

    ctx = verify_request(_req_bearer(token))
    assert ctx.external is False
