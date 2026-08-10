"""Read-only group + member + balance + settlement exposure for the agent.

Splits are written through `propose_create_transaction` (with its
`group_id` + `splits` parameters) — there's no `propose_create_split`
tool because splits live attached to the parent transaction, not as
standalone rows.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import balance_service, group_service, settlement_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_uuid, resolve_workspace_id


@tool(
    name="list_groups",
    description=(
        "List the user's expense-sharing groups (Splitwise-style: 'Amigos', "
        "'Roommates', etc.) along with their members. Returns each group "
        "with `members: [{id, name, is_self}]` so a single call gives the "
        "model everything it needs to propose a transaction with equal/"
        "exact/percent splits. The `is_self` flag marks the member that "
        "represents the user (used to compute who-owes-who balances)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "include_archived": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    tags=["read", "groups"],
)
async def list_groups(
    *,
    session: AsyncSession,
    ctx: CallContext,
    include_archived: bool = False,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    groups = await group_service.list_groups(session, ws_id, ctx.user_id, include_archived=include_archived)
    return {
        "items": [
            {
                "id": str(g.id),
                "name": g.name,
                "kind": g.kind,
                "default_currency": g.default_currency,
                "is_archived": bool(g.is_archived),
                "members": [
                    {
                        "id": str(m.id),
                        "name": m.name,
                        "is_self": bool(m.is_self),
                    }
                    for m in (g.members or [])
                ],
            }
            for g in groups
        ],
        "total": len(groups),
    }


@tool(
    name="get_group_balances",
    description=(
        "Compute the who-owes-who balance for one expense-sharing group. "
        "Returns lines per member: positive `amount` means the member "
        "OWES the user (self) that much in `currency`; negative means the "
        "user owes them. `amount_in_default_currency` is the value "
        "converted to the group's default currency for a single bottom "
        "line. Already accounts for past `group_settlements` (payments "
        "that closed previous balances). Use this for 'quem ainda me "
        "deve?', 'estamos quites?', 'qual o saldo do grupo Amigos?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "group_id": {"type": "string", "format": "uuid"},
        },
        "required": ["group_id"],
        "additionalProperties": False,
    },
    tags=["read", "groups"],
)
async def get_group_balances(
    *,
    session: AsyncSession,
    ctx: CallContext,
    group_id: str,
) -> dict[str, Any]:
    gid = parse_uuid(group_id)
    if gid is None:
        return dict(error="group not found or not visible to this user")


    ws_id = await resolve_workspace_id(session, ctx)
    result = await balance_service.compute_balances(session, gid, ws_id, ctx.user_id)
    if result is None:
        return {"error": "group not found or not visible to this user"}

    # Resolve member names so the agent doesn't need a second list_groups
    # call to humanize the output.
    from sqlalchemy import select
    from app.models.group import GroupMember
    rows = (await session.execute(
        select(GroupMember).where(GroupMember.group_id == gid)
    )).scalars().all()
    name_by_id = {m.id: m.name for m in rows}
    is_self_by_id = {m.id: bool(m.is_self) for m in rows}

    lines = []
    for ln in result.get("lines", []):
        mid = ln["member_id"]
        lines.append({
            "member_id": str(mid),
            "member_name": name_by_id.get(mid),
            "is_self": is_self_by_id.get(mid, False),
            "currency": ln["currency"],
            "amount": num(ln["amount"]),
            "amount_in_default_currency": num(ln.get("amount_in_default_currency")),
        })
    return {
        "group_id": str(result["group_id"]),
        "self_member_id": str(result["self_member_id"]) if result.get("self_member_id") else None,
        "default_currency": result.get("default_currency"),
        "lines": lines,
    }


@tool(
    name="list_group_settlements",
    description=(
        "List the recorded settlements (payments between members that "
        "close out balances) for one group, newest first. Each row has "
        "{from_member_id, to_member_id, amount, currency, date, notes}. "
        "Pair with `get_group_balances` when the user asks 'quem já me "
        "pagou?' or 'qual o histórico de acertos?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "group_id": {"type": "string", "format": "uuid"},
        },
        "required": ["group_id"],
        "additionalProperties": False,
    },
    tags=["read", "groups"],
)
async def list_group_settlements(
    *,
    session: AsyncSession,
    ctx: CallContext,
    group_id: str,
) -> dict[str, Any]:
    gid = parse_uuid(group_id)
    if gid is None:
        return dict(error="group not found or not visible to this user")

    ws_id = await resolve_workspace_id(session, ctx)
    rows = await settlement_service.list_settlements(session, gid, ws_id, ctx.user_id)
    if rows is None:
        return {"error": "group not found or not visible to this user"}

    items = [
        {
            "id": str(s.id),
            "group_id": str(s.group_id),
            "from_member_id": str(s.from_member_id),
            "to_member_id": str(s.to_member_id),
            "amount": num(s.amount),
            "currency": s.currency,
            "date": s.date.isoformat() if s.date else None,
            "notes": getattr(s, "notes", None),
            "transaction_id": str(s.transaction_id) if getattr(s, "transaction_id", None) else None,
        }
        for s in rows
    ]
    return {"items": items, "total": len(items)}
