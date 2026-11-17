from __future__ import annotations

import time
import uuid
from typing import Optional

from jose import jwt

from app.agents.config import get_agent_settings


JWT_ISSUER = "securo-backend"
JWT_AUDIENCE = "securo-mcp"
JWT_ALGO = "HS256"
# Prefix for the audience of tokens sent to user-supplied MCP servers. The
# built-in server only accepts `JWT_AUDIENCE`, so a token handed to a third
# party can never be replayed against it.
EXTRA_AUDIENCE_PREFIX = "securo-mcp-extra:"


def extra_server_audience(server_name: str) -> str:
    """Audience for tokens sent to the user-supplied MCP server `server_name`."""
    return f"{EXTRA_AUDIENCE_PREFIX}{server_name}"


def mint_token(
    *,
    user_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID] = None,
    conversation_id: Optional[uuid.UUID] = None,
    agent_id: Optional[uuid.UUID] = None,
    ttl_seconds: Optional[int] = None,
    external: bool = False,
    audience: str = JWT_AUDIENCE,
) -> str:
    """Mint an MCP JWT scoped to a (user, workspace) pair.

    `workspace_id` is recommended for every internal call so MCP tools
    operate within the right tenant. It's optional only because long-
    lived external tokens issued before the multi-workspace migration
    still verify: the MCP server falls back to the user's default
    workspace when the claim is absent.

    `audience` defaults to the built-in server. Tokens for any other server
    must use `extra_server_audience(name)` so they are useless against it.
    """
    s = get_agent_settings()
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "iss": JWT_ISSUER,
        "aud": audience,
        "iat": now,
        "exp": now + (ttl_seconds or s.mcp_jwt_ttl_seconds),
    }
    if workspace_id:
        payload["ws_id"] = str(workspace_id)
    if conversation_id:
        payload["conv_id"] = str(conversation_id)
    if agent_id:
        payload["agent_id"] = str(agent_id)
    if external:
        payload["ext"] = True
    return jwt.encode(payload, s.mcp_jwt_secret, algorithm=JWT_ALGO)
