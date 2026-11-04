"""Mint long-lived MCP tokens for external agents.

Lets a logged-in user generate a JWT they can paste into Claude Desktop,
n8n, or any other MCP client. The token is signed with the same
`AGENTS_MCP_JWT_SECRET` the internal runtime uses, scoped to the calling
user AND their active workspace, with a configurable TTL (default 90
days) and an `ext: true` claim. The MCP server already verifies any
valid JWT — no auth changes needed there.

External tokens are bound to one workspace at creation time. Users with
multiple workspaces issue one token per workspace (they switch contexts
in the UI before issuing) so external agents always land in a
predictable tenant.

Follows the AGENTS_ENABLED master switch: when agents are off, the
router isn't mounted at all so the endpoint 404s.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.agents.config import get_agent_settings
from app.agents.mcp.auth import mint_token
from app.core.workspace_context import WorkspaceContext, current_writable_workspace

router = APIRouter(prefix="/api/agents/mcp-tokens", tags=["agents"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_mcp_token(ctx: WorkspaceContext = Depends(current_writable_workspace)):
    """Mint a long-lived MCP token for an external client.

    Write-gated because of what the token can do, not because minting
    writes a row. It carries (user, workspace) to the MCP server, whose
    tool set includes `propose_create_transaction`, `propose_create_budget`
    and friends — all of which persist. Handing a read-only member a
    credential that writes would route around the gate the HTTP API
    enforces.
    """
    s = get_agent_settings()
    ttl_seconds = max(s.mcp_external_ttl_days, 1) * 86400
    token = mint_token(
        user_id=ctx.user_id,
        workspace_id=ctx.workspace.id,
        ttl_seconds=ttl_seconds,
        external=True,
    )
    return {
        "token": token,
        "expires_in_seconds": ttl_seconds,
        "expires_in_days": s.mcp_external_ttl_days,
        "workspace_id": str(ctx.workspace.id),
        "workspace_name": ctx.workspace.name,
    }
