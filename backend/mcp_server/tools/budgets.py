from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import budget_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_date, resolve_workspace_id


@tool(
    name="get_budget_vs_actual",
    description=(
        "For each budgeted category in the given month, return budget amount, "
        "actual spending, and the variance. Defaults to the current month."
    ),
    parameters={
        "type": "object",
        "properties": {
            "month": {
                "type": "string",
                "format": "date",
                "description": "Any date inside the target month (YYYY-MM-DD)",
            },
        },
        "additionalProperties": False,
    },
    tags=["read", "budgets"],
)
async def get_budget_vs_actual(
    *,
    session: AsyncSession,
    ctx: CallContext,
    month: str | None = None,
) -> dict[str, Any]:
    target = parse_date(month) or date.today().replace(day=1)
    ws_id = await resolve_workspace_id(session, ctx)
    rows = await budget_service.get_budget_vs_actual(session, ws_id, ctx.user_id, month=target)
    items = []
    for r in rows:
        # BudgetVsActual is a Pydantic model; serialize defensively.
        d = r.model_dump() if hasattr(r, "model_dump") else r.__dict__
        items.append(
            {
                "category_id": str(d.get("category_id")) if d.get("category_id") else None,
                "category_name": d.get("category_name"),
                "budget_amount": num(d.get("budget_amount") or d.get("amount")),
                "actual_amount": num(d.get("actual_amount") or d.get("actual") or d.get("spent")),
                "remaining": num(d.get("remaining")),
                "currency": d.get("currency"),
            }
        )
    return {"month": target.isoformat(), "items": items, "total": len(items)}
