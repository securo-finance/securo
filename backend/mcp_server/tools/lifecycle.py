"""Lifecycle entities — recurring transactions, assets, goals, budgets.

These come up in 'do I have any subscriptions?' / 'how are my goals
going?' / 'list my investments' type questions. Surfacing them as
first-class tools keeps the agent from falling back to text search.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import asset_service, budget_service, goal_service, recurring_transaction_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_date, resolve_workspace_id


@tool(
    name="list_recurring_transactions",
    description=(
        "List the user's recurring transactions / subscriptions. Each row "
        "has frequency (weekly/monthly/...), next_occurrence, amount, "
        "category, and account. Use this — not list_transactions search — "
        "to answer 'what subscriptions do I have?' or 'show my recurring "
        "expenses'."
    ),
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    tags=["read", "recurring"],
)
async def list_recurring_transactions(
    *, session: AsyncSession, ctx: CallContext
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rows = await recurring_transaction_service.get_recurring_transactions(session, ws_id)
    items = [
        {
            "id": str(r.id),
            "description": r.description,
            "amount": num(r.amount),
            "currency": r.currency,
            "type": r.type,
            "frequency": r.frequency,
            "weekend_adjustment": r.weekend_adjustment,
            "interval": getattr(r, "interval", None),
            "next_occurrence": r.next_occurrence.isoformat() if getattr(r, "next_occurrence", None) else None,
            "start_date": r.start_date.isoformat() if getattr(r, "start_date", None) else None,
            "end_date": r.end_date.isoformat() if r.end_date else None,
            "is_active": bool(getattr(r, "is_active", True)),
            "category_id": str(r.category_id) if getattr(r, "category_id", None) else None,
            "account_id": str(r.account_id) if getattr(r, "account_id", None) else None,
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}


@tool(
    name="list_assets",
    description=(
        "List the user's investments / assets (stocks, crypto, CDBs, real "
        "estate, etc.) with current value and grouping. Use for 'what "
        "investments do I have?' or 'show my portfolio'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "include_archived": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    tags=["read", "assets"],
)
async def list_assets(
    *, session: AsyncSession, ctx: CallContext, include_archived: bool = False
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rows = await asset_service.get_assets(session, ws_id, include_archived=include_archived)
    items: list[dict[str, Any]] = []
    for r in rows:
        d = r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r.__dict__)
        items.append({
            "id": str(d.get("id")) if d.get("id") else None,
            "name": d.get("name"),
            "type": d.get("type"),
            "currency": d.get("currency"),
            "current_value": num(d.get("current_value")),
            "current_value_primary": num(d.get("current_value_primary")),
            "ticker": d.get("ticker"),
            "units": num(d.get("units")),
            "group_id": str(d.get("group_id")) if d.get("group_id") else None,
            "is_archived": bool(d.get("is_archived", False)),
        })
    return {"items": items, "total": len(items)}


@tool(
    name="list_goals",
    description=(
        "List the user's savings / financial goals with target, current "
        "amount, deadline, and progress. Use for 'how are my goals "
        "going?' / 'am I on track?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Filter by status (e.g. 'active', 'completed')"},
        },
        "additionalProperties": False,
    },
    tags=["read", "goals"],
)
async def list_goals(
    *, session: AsyncSession, ctx: CallContext, status: str | None = None
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rows = await goal_service.get_goals(session, ws_id, ctx.user_id, status=status)
    items: list[dict[str, Any]] = []
    for r in rows:
        d = r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r.__dict__)
        items.append({
            "id": str(d.get("id")) if d.get("id") else None,
            "name": d.get("name"),
            "target_amount": num(d.get("target_amount")),
            "current_amount": num(d.get("current_amount")),
            "currency": d.get("currency"),
            "deadline": d.get("deadline"),
            "status": d.get("status"),
            "progress_pct": num(d.get("progress_pct")),
            "category_id": str(d.get("category_id")) if d.get("category_id") else None,
        })
    return {"items": items, "total": len(items)}


@tool(
    name="list_budgets",
    description=(
        "List the raw budget rows (category + monthly amount). For "
        "spending vs budget comparison use get_budget_vs_actual instead."
    ),
    parameters={
        "type": "object",
        "properties": {
            "month": {"type": "string", "format": "date", "description": "Any date inside the target month; omit for all months"},
        },
        "additionalProperties": False,
    },
    tags=["read", "budgets"],
)
async def list_budgets(
    *, session: AsyncSession, ctx: CallContext, month: str | None = None
) -> dict[str, Any]:
    target = parse_date(month)
    ws_id = await resolve_workspace_id(session, ctx)
    rows = await budget_service.get_budgets(session, ws_id, month=target)
    items = [
        {
            "id": str(b.id),
            "category_id": str(b.category_id) if b.category_id else None,
            "amount": num(b.amount),
            "month": b.month.isoformat() if getattr(b, "month", None) else None,
            "is_recurring": bool(getattr(b, "is_recurring", False)),
        }
        for b in rows
    ]
    return {"items": items, "total": len(items)}
