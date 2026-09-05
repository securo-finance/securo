from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import payee_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_workspace_id


@tool(
    name="list_payees",
    description="List the user's payees (merchants/counterparties).",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    tags=["read", "payees"],
)
async def list_payees(
    *,
    session: AsyncSession,
    ctx: CallContext,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    payees = await payee_service.get_payees(session, ws_id)
    items = [
        {
            "id": str(p.id),
            "name": p.name,
        }
        for p in payees
    ]
    return {"items": items, "total": len(items)}
