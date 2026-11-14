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
    # The caller's role in `workspace_id`, set by `authorize_call` from the
    # database on every tool call. None only when a handler is invoked
    # directly (tests), never on a request that went through the server.
    role: Optional[str] = None

    @property
    def can_write(self) -> bool:
        """False only for a role known to be read-only.

        Mirrors `WorkspaceContext.can_write` on the HTTP side.
        """
        return self.role is None or self.role in WRITE_ROLES


WRITE_ROLES = frozenset({"owner", "editor", "manager"})


class ToolAccessDenied(PermissionError):
    """The token is valid but its user may not act in the workspace."""


async def authorize_call(session, ctx: CallContext) -> uuid.UUID:
    """Check, against the database, that the caller may use the workspace.

    A token only proves who minted it and when. Membership can change during
    its lifetime (external tokens live for months), so the `ws_id` claim is
    re-checked on every call, the same way the HTTP API resolves
    `X-Workspace-Id`: the user must be active, the workspace must exist and
    not be archived, and the user must be a member or its manager.

    On success, pins `ctx.workspace_id` to the checked workspace (so tools
    never fall back to a different default), sets `ctx.role`, and returns
    the workspace id.
    """
    from app.models.user import User
    from app.models.workspace import MANAGER_VIRTUAL_ROLE, Workspace
    from app.services.workspace_service import (
        get_default_workspace,
        get_membership,
        is_workspace_manager,
    )

    user = await session.get(User, ctx.user_id)
    if user is None or not user.is_active:
        raise ToolAccessDenied("This token's user is no longer active.")

    workspace_id = ctx.workspace_id
    if workspace_id is None:
        default = await get_default_workspace(session, ctx.user_id)
        if default is None:
            raise ToolAccessDenied("No workspace available for this user.")
        workspace_id = default.id

    workspace = await session.get(Workspace, workspace_id)
    if workspace is None or workspace.is_archived:
        raise ToolAccessDenied("Workspace not found.")

    member = await get_membership(session, workspace_id, ctx.user_id)
    if member is not None:
        role = member.role
    elif await is_workspace_manager(session, workspace_id, ctx.user_id):
        role = MANAGER_VIRTUAL_ROLE
    else:
        # Same answer as the HTTP API: don't confirm the workspace exists.
        raise ToolAccessDenied("Workspace not found.")

    ctx.workspace_id = workspace_id
    ctx.role = role
    return workspace_id


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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {exc}") from exc

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
