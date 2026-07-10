from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import search_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_workspace_id


@tool(
    name="search_all",
    description=(
        "Global text search across the user's entities (transactions, "
        "accounts, categories, payees, etc.). Returns up to N hits per type."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "per_type_limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 5},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    tags=["read", "search"],
)
async def search_all(
    *,
    session: AsyncSession,
    ctx: CallContext,
    query: str,
    per_type_limit: int = 5,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    hits = await search_service.search_all(
        session, ws_id, ctx.user_id, query, per_type_limit=int(per_type_limit)
    )
    return {"items": hits, "total": len(hits)}
