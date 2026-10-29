"""Lifecycle entities — recurring transactions, assets, goals, budgets.

These come up in 'do I have any subscriptions?' / 'how are my goals
going?' / 'list my investments' type questions. Surfacing them as
first-class tools keeps the agent from falling back to text search.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_group import AssetGroup
from app.services import (
    asset_service,
    asset_transaction_service,
    budget_service,
    goal_service,
    recurring_transaction_service,
)
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_date, parse_uuid, resolve_workspace_id


async def _group_names(session: AsyncSession, workspace_id) -> dict[str, str]:
    """id -> name for the workspace's asset groups ("wallets").

    A plain two-column select, not asset_group_service.get_groups: that one
    rolls up values and resolves institutions per group, which is wasted
    work when all we need is a label.
    """
    result = await session.execute(
        select(AssetGroup.id, AssetGroup.name).where(AssetGroup.workspace_id == workspace_id)
    )
    return {str(gid): name for gid, name in result.all()}


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
        "investments do I have?' or 'show my portfolio'. Ledger-backed "
        "holdings also carry cost basis: `average_price` (weighted-average "
        "cost per unit), `total_invested` (cost basis of the units still "
        "held), `gain_loss` (unrealized), `realized_gain` (from past sells) "
        "and `transaction_count`. A `transaction_count` of 0 means the "
        "holding has no ledger, so its cost fields are null — answer 'how "
        "much did I pay for X?' from these fields, and use "
        "list_asset_transactions for the individual buys and sells. Every "
        "money field is in that holding's own `currency`, so do NOT add "
        "them up across holdings unless the currencies match: a portfolio "
        "mixing EUR and USD needs converting first, and summing as-is "
        "silently reports a 1:1 total."
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
    group_names = await _group_names(session, ws_id)
    items: list[dict[str, Any]] = []
    for r in rows:
        d = r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r.__dict__)
        group_id = str(d.get("group_id")) if d.get("group_id") else None
        items.append({
            "id": str(d.get("id")) if d.get("id") else None,
            "name": d.get("name"),
            "type": d.get("type"),
            "currency": d.get("currency"),
            "current_value": num(d.get("current_value")),
            "current_value_primary": num(d.get("current_value_primary")),
            "ticker": d.get("ticker"),
            "units": num(d.get("units")),
            "group_id": group_id,
            "group_name": group_names.get(group_id) if group_id else None,
            "average_price": num(d.get("average_price")),
            "total_invested": num(d.get("total_invested")),
            "gain_loss": num(d.get("gain_loss")),
            "realized_gain": num(d.get("realized_gain")),
            "transaction_count": int(d.get("transaction_count") or 0),
            "is_archived": bool(d.get("is_archived", False)),
        })
    return {"items": items, "total": len(items)}


@tool(
    name="list_asset_transactions",
    description=(
        "List the buy/sell entries of the user's investment ledger, newest "
        "first. Use for 'when did I buy X?', 'what did I pay for X?' or "
        "'show my orders'. Filter by asset_id, ticker, kind (buy/sell) and "
        "date range; every filter is applied before the limit, so narrowing "
        "a range returns the newest rows inside it. Each row carries "
        "quantity, price per unit (in the asset's currency), fee, date, "
        "source and the asset's name and ticker. Dividends and staking "
        "rewards are NOT here: the ledger only records buys and sells. For "
        "the resulting position and cost basis use list_assets."
    ),
    parameters={
        "type": "object",
        "properties": {
            "asset_id": {"type": "string", "format": "uuid", "description": "Restrict to one holding (id from list_assets)"},
            "ticker": {"type": "string", "description": "Restrict by ticker, e.g. 'MSFT' or 'BTC-EUR'. Case-insensitive."},
            "kind": {"type": "string", "enum": ["buy", "sell"], "description": "Only purchases or only sales"},
            "from_date": {"type": "string", "format": "date", "description": "Inclusive lower bound (YYYY-MM-DD)"},
            "to_date": {"type": "string", "format": "date", "description": "Inclusive upper bound (YYYY-MM-DD)"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100, "description": "Max rows (default 100, capped at 500)"},
        },
        "additionalProperties": False,
    },
    tags=["read", "assets"],
)
async def list_asset_transactions(
    *,
    session: AsyncSession,
    ctx: CallContext,
    asset_id: str | None = None,
    ticker: str | None = None,
    kind: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rows = await asset_transaction_service.list_workspace_transactions(
        session,
        ws_id,
        asset_id=parse_uuid(asset_id),
        ticker=ticker,
        # The service drops a kind it does not recognise, which would hand
        # back an unfiltered list that looks filtered. Normalise the casing
        # an LLM is likely to send rather than let "Buy" silently widen it.
        kind=kind.lower() if kind else None,
        from_date=parse_date(from_date),
        to_date=parse_date(to_date),
        limit=max(1, min(int(limit), 500)),
    )
    items = [
        {
            "id": str(t.id),
            "asset_id": str(t.asset_id),
            "asset_name": t.asset_name,
            "ticker": t.ticker,
            "kind": t.kind,
            "quantity": num(t.quantity),
            "price": num(t.price),
            "fee": num(t.fee),
            "currency": t.currency,
            "date": t.date.isoformat() if t.date else None,
            "source": t.source,
            "notes": t.notes,
        }
        for t in rows
    ]
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
