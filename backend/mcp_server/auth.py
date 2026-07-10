"""JWT verification for MCP requests.

The agent runtime (in the backend) mints a short-lived JWT per call,
signed with `AGENTS_MCP_JWT_SECRET`. We verify here. Same secret on both
sides; mismatched secret = 401 every time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt

from app.agents.config import get_agent_settings


JWT_ISSUER = "securo-backend"
JWT_AUDIENCE = "securo-mcp"
JWT_ALGO = "HS256"


@dataclass
class CallContext:
    user_id: uuid.UUID
    # Workspace the tool call operates in. Populated from the JWT's
    # `ws_id` claim when present; otherwise resolved lazily by tools to
    # the user's default workspace (backwards-compat for tokens minted
    # before the workspace migration).
    workspace_id: Optional[uuid.UUID] = None
    conversation_id: Optional[uuid.UUID] = None
    agent_id: Optional[uuid.UUID] = None
    # True when the JWT was minted for an external agent (Claude Desktop,
    # n8n, etc.) rather than Securo's own runtime. Used for log tagging;
    # tool authorization is identical (same user scope).
    external: bool = False


def _settings():
    return get_agent_settings()


def verify_request(request: Request) -> CallContext:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(
            token,
            _settings().mcp_jwt_secret,
            algorithms=[JWT_ALGO],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {exc}"
        ) from exc

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing subject")
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad subject") from exc

    conv_raw = payload.get("conv_id")
    agent_raw = payload.get("agent_id")
    ws_raw = payload.get("ws_id")
    return CallContext(
        user_id=user_id,
        workspace_id=uuid.UUID(ws_raw) if ws_raw else None,
        conversation_id=uuid.UUID(conv_raw) if conv_raw else None,
        agent_id=uuid.UUID(agent_raw) if agent_raw else None,
        external=bool(payload.get("ext")),
    )
