from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import account_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_date, parse_uuid, resolve_workspace_id


@tool(
    name="list_accounts",
    description=(
        "List the user's accounts (checking, savings, credit cards, wallets, etc.) "
        "with current balances. Closed accounts are excluded by default."
    ),
    parameters={
        "type": "object",
        "properties": {
            "include_closed": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    tags=["read", "accounts"],
)
async def list_accounts(
    *,
    session: AsyncSession,
    ctx: CallContext,
    include_closed: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rows = await account_service.get_accounts(session, ws_id, include_closed=include_closed)
    # rows is already a list of dicts (per service contract), but normalize keys.
    items: list[dict[str, Any]] = []
    for r in rows:
        items.append({
            "id": str(r.get("id")) if r.get("id") else None,
            "name": r.get("name"),
            "type": r.get("type"),
            "currency": r.get("currency"),
            "balance": num(r.get("balance")),
            "balance_primary": num(r.get("balance_primary")),
            "is_closed": bool(r.get("is_closed", False)),
            "institution": r.get("institution_name"),
        })
    return {"items": items, "total": len(items)}


@tool(
    name="get_account_summary",
    description=(
        "Income, expenses, and net for a single account over a date range. "
        "Defaults to the current month if no range is provided."
    ),
    parameters={
        "type": "object",
        "properties": {
            "account_id": {"type": "string", "format": "uuid"},
            "from_date": {"type": "string", "format": "date"},
            "to_date": {"type": "string", "format": "date"},
        },
        "required": ["account_id"],
        "additionalProperties": False,
    },
    tags=["read", "accounts"],
)
async def get_account_summary(
    *,
    session: AsyncSession,
    ctx: CallContext,
    account_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    account_uuid = parse_uuid(account_id)
    if account_uuid is None:
        return dict(error="account not found")

    summary = await account_service.get_account_summary(
        session,
        account_uuid,
        ws_id,
        date_from=parse_date(from_date),
        date_to=parse_date(to_date),
    )
    if summary is None:
        return {"error": "account not found"}
    # Normalize numeric fields.
    for k in list(summary.keys()):
        v = summary[k]
        if hasattr(v, "isoformat"):
            summary[k] = v.isoformat()
    return summary
