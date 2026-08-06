from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import GroupMember
from app.services import transaction_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import num, parse_date, parse_uuid, parse_uuid_list, resolve_workspace_id


@tool(
    name="list_transactions",
    description=(
        "List the user's transactions with rich filters. Returns paginated "
        "items plus a `total` count of ALL matches (not just the page). "
        "If you only need a count (e.g. 'how many uncategorized?'), call "
        "with limit=1 and read `total` instead of paginating through "
        "everything. By default sorts by purchase date (most recent "
        "first) so 'what's my last transaction?' returns the actual "
        "newest one — change sort_by to 'date' for the credit-card-bill-"
        "aware ordering used by the dashboard. Each row carries its "
        "native currency in `currency` and the user's primary-currency "
        "view in `amount_primary`. To answer 'do I have any EUR/USD/etc "
        "transactions?' use the `currency` filter — don't text-search "
        "the description. Each row also carries a `splits` field: when "
        "non-null, the transaction is shared via a Splitwise-style "
        "group — `splits.group_id` ties it back to a row from "
        "`list_groups`, and `splits.members[]` shows each "
        "{member_id, member_name, is_self, share_amount, share_type, "
        "share_pct}. So 'is this transaction split?' is just `splits != "
        "null`; no extra tool call needed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "account_ids": {"type": "array", "items": {"type": "string", "format": "uuid"}, "description": "Filter to specific accounts (by id)"},
            "account_types": {
                "type": "array",
                "items": {"type": "string", "enum": ["checking", "savings", "credit_card", "wallet", "investment", "loan", "other"]},
                "description": "Filter by account type — e.g. ['credit_card'] for 'all my credit-card transactions'",
            },
            "category_ids": {"type": "array", "items": {"type": "string", "format": "uuid"}, "description": "Filter to specific categories"},
            "payee_id": {"type": "string", "format": "uuid", "description": "Filter to a single payee"},
            "group_id": {"type": "string", "format": "uuid", "description": "Filter to transactions split with this expense-sharing group (Splitwise-style). The id comes from `list_groups`. Use this — NOT a `search:'group_id:...'` hack — to ask 'show all transactions in group X'."},
            "from_date": {"type": "string", "format": "date", "description": "Inclusive lower bound (YYYY-MM-DD)"},
            "to_date": {"type": "string", "format": "date", "description": "Inclusive upper bound (YYYY-MM-DD)"},
            "search": {"type": "string", "description": "Substring match against description or payee"},
            "currency": {
                "type": "string",
                "description": "ISO currency code (BRL, USD, EUR, GBP, ...). Matches each transaction's native currency exactly. Use this — not search — for currency questions.",
            },
            "min_amount": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Minimum absolute amount (in user's primary currency when available, else native). E.g. 100 = 'transactions of $100 or more'.",
            },
            "max_amount": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Maximum absolute amount (primary-currency view).",
            },
            "tx_type": {"type": "string", "enum": ["debit", "credit"], "description": "debit = expense, credit = income"},
            "status": {"type": "string", "enum": ["posted", "pending"], "description": "Filter by posting status: posted = settled, pending = not yet settled."},
            "uncategorized": {"type": "boolean", "default": False, "description": "Only transactions without a category"},
            "exclude_transfers": {"type": "boolean", "default": False, "description": "Exclude transfers between user's own accounts"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Match any of these tags"},
            "sort_by": {
                "type": "string",
                "enum": ["transaction_date", "date", "amount", "description", "payee", "category", "account", "created_at"],
                "default": "transaction_date",
                "description": (
                    "transaction_date = purchase date (default, intuitive 'latest'); "
                    "date = cycle/accrual-aware (matches dashboard ordering, can put "
                    "future-billed credit-card purchases on top); "
                    "created_at = order rows were inserted into Securo (useful for "
                    "freshly-synced data)."
                ),
            },
            "sort_dir": {"type": "string", "enum": ["desc", "asc"], "default": "desc"},
            "accounting_mode": {
                "type": "string",
                "enum": ["cash", "accrual"],
                "description": "Override the global accounting mode for this query. Affects how credit-card transactions are bucketed by date.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 25, "description": "Max rows per page. Capped at 50 — bigger pages blow up token budgets on small models. Use `total` for counts and pagination via `page` to walk longer lists."},
            "page": {"type": "integer", "minimum": 1, "default": 1},
        },
        "additionalProperties": False,
    },
    tags=["read", "transactions"],
)
async def list_transactions(
    *,
    session: AsyncSession,
    ctx: CallContext,
    account_ids: list[str] | None = None,
    account_types: list[str] | None = None,
    category_ids: list[str] | None = None,
    payee_id: str | None = None,
    group_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    search: str | None = None,
    currency: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    tx_type: str | None = None,
    status: str | None = None,
    uncategorized: bool = False,
    exclude_transfers: bool = False,
    tags: list[str] | None = None,
    sort_by: str = "transaction_date",
    sort_dir: str = "desc",
    accounting_mode: str | None = None,
    limit: int = 25,
    page: int = 1,
) -> dict[str, Any]:
    # Hard cap regardless of what the LLM asks — the schema says 50 but
    # not every provider enforces additionalProperties / maximum.
    limit = max(1, min(int(limit), 50))
    ws_id = await resolve_workspace_id(session, ctx)
    txs, total, _ = await transaction_service.get_transactions(
        session,
        ws_id,
        ctx.user_id,
        account_ids=parse_uuid_list(account_ids),
        account_types=account_types or None,
        category_ids=parse_uuid_list(category_ids),
        payee_id=parse_uuid(payee_id) if payee_id else None,
        group_id=parse_uuid(group_id) if group_id else None,
        from_date=parse_date(from_date),
        to_date=parse_date(to_date),
        search=search,
        currency=currency,
        min_amount=float(min_amount) if min_amount is not None else None,
        max_amount=float(max_amount) if max_amount is not None else None,
        txn_type=tx_type,
        status=status,
        uncategorized=uncategorized,
        exclude_transfers=exclude_transfers,
        tags=tags or None,
        sort_by=sort_by,
        sort_dir=sort_dir,
        accounting_mode=accounting_mode,
        limit=int(limit),
        page=int(page),
    )
    # Resolve group + member metadata for any transaction with splits, in
    # one batched query — without this the agent has no way to tell that
    # a transaction is split among a group's members ("is this Jantar a
    # solo expense?" → "no, split with Marcelo + Tereza in Amigos").
    member_ids: set[Any] = set()
    for t in txs:
        for s in (getattr(t, "splits", None) or []):
            member_ids.add(s.group_member_id)
    member_lookup: dict[Any, GroupMember] = {}
    if member_ids:
        rows = (await session.execute(
            select(GroupMember).where(GroupMember.id.in_(member_ids))
        )).scalars().all()
        member_lookup = {m.id: m for m in rows}

    def _splits_summary(t: Any) -> dict[str, Any] | None:
        splits = getattr(t, "splits", None) or []
        if not splits:
            return None
        # Splits all belong to the same group by invariant — surface the
        # group id from any one of them so the model can correlate with
        # `list_groups` without a second tool call.
        group_id = None
        items = []
        for s in splits:
            mem = member_lookup.get(s.group_member_id)
            if mem is not None and group_id is None:
                group_id = str(mem.group_id)
            items.append({
                "member_id": str(s.group_member_id),
                "member_name": mem.name if mem else None,
                "is_self": bool(getattr(mem, "is_self", False)) if mem else False,
                "share_amount": num(s.share_amount),
                "share_type": s.share_type,
                "share_pct": num(s.share_pct),
            })
        return {
            "is_split": True,
            "group_id": group_id,
            "share_type": splits[0].share_type if splits else None,
            "members": items,
        }

    items = [
        {
            "id": str(t.id),
            "date": t.date.isoformat() if t.date else None,
            "effective_date": t.effective_date.isoformat() if getattr(t, "effective_date", None) else None,
            "description": t.description,
            "amount": num(t.amount),
            "currency": t.currency,
            "amount_primary": num(getattr(t, "amount_primary", None)),
            "type": t.type,
            "status": getattr(t, "status", None),
            "category_id": str(t.category_id) if t.category_id else None,
            "category_name": t.category.name if t.category else None,
            "account_id": str(t.account_id) if t.account_id else None,
            "account_name": t.account.name if getattr(t, "account", None) else None,
            "payee_id": str(t.payee_id) if getattr(t, "payee_id", None) else None,
            "payee_name": t.payee_entity.name if t.payee_entity else None,
            "tags": getattr(t, "tags", None),
            "is_transfer": bool(getattr(t, "transfer_pair_id", None)),
            "notes": getattr(t, "notes", None),
            "splits": _splits_summary(t),
        }
        for t in txs
    ]
    return {
        "items": items,
        "total": int(total),
        "page": int(page),
        "limit": int(limit),
        "sort_by": sort_by,
        "sort_dir": sort_dir,
    }
