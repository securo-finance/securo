from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.models.category import Category
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_categories, num, parse_date, parse_uuid_list, resolve_workspace_id


@tool(
    name="aggregate",
    description=(
        "Aggregate transactions without writing custom SQL. Choose a metric "
        "(sum|count|avg) and a group_by (category|month|account|payee|day). "
        "Returns a list of {bucket, value, count} rows. Amounts use the "
        "user's primary currency when available, falling back to native. "
        "By default counts only POSTED transactions (already settled) — "
        "set status='all' or 'pending' to include scheduled/recurring rows. "
        "Because of that default (and exclude_transfers=True) a monthly total "
        "here can be lower than the Dashboard's income/expense figures, which "
        "also count pending rows — for 'how much did I spend this month?' "
        "prefer get_dashboard_snapshot and use aggregate for breakdowns. "
        "To filter by category, pass category_ids or category_names; "
        "description_contains searches transaction text, not category names. "
        "Quote `grand_total` for the overall figure; `items` are the per-bucket rows."
    ),
    parameters={
        "type": "object",
        "properties": {
            "metric": {"type": "string", "enum": ["sum", "count", "avg"], "default": "sum"},
            "group_by": {"type": "string", "enum": ["category", "month", "account", "payee", "day"], "default": "category"},
            "from_date": {"type": "string", "format": "date"},
            "to_date": {"type": "string", "format": "date"},
            "account_ids": {"type": "array", "items": {"type": "string", "format": "uuid"}},
            "category_ids": {"type": "array", "items": {"type": "string", "format": "uuid"}},
            "category_names": {"type": "array", "items": {"type": "string"}, "description": "Category names (case-insensitive) when you don't have ids yet. Unknown names return did_you_mean suggestions instead of silently matching nothing."},
            "uncategorized": {"type": "boolean", "default": False, "description": "Only transactions without a category (e.g. 'how much is still uncategorized?')."},
            "payee_id": {"type": "string", "format": "uuid", "description": "Restrict to a single payee — useful for 'how much did I spend at X?'"},
            "currency": {"type": "string", "description": "Restrict to one native currency (BRL, USD, EUR, ...)"},
            "tx_type": {"type": "string", "enum": ["expense", "income"], "description": "Filter to expenses or income only"},
            "description_contains": {"type": "string", "description": "Case-insensitive substring match against the transaction description — use this to scope to a merchant/keyword like 'uber', 'spotify', 'amigos do bem'."},
            "status": {"type": "string", "enum": ["posted", "pending", "all"], "default": "posted", "description": "Default 'posted' = only money that already moved. Use 'pending' for scheduled/recurring not yet settled, or 'all' to include both."},
            "exclude_transfers": {"type": "boolean", "default": True},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 25, "description": "Max bucket rows in the result. Capped at 50."},
        },
        "additionalProperties": False,
    },
    tags=["read", "aggregate"],
)
async def aggregate(
    *,
    session: AsyncSession,
    ctx: CallContext,
    metric: str = "sum",
    group_by: str = "category",
    from_date: str | None = None,
    to_date: str | None = None,
    account_ids: list[str] | None = None,
    category_ids: list[str] | None = None,
    category_names: list[str] | None = None,
    uncategorized: bool = False,
    payee_id: str | None = None,
    currency: str | None = None,
    tx_type: str | None = None,
    description_contains: str | None = None,
    status: str = "posted",
    exclude_transfers: bool = True,
    limit: int = 25,
) -> dict[str, Any]:
    # Hard cap regardless of LLM input — keeps payload small enough for
    # token-tight providers.
    limit = max(1, min(int(limit), 50))
    ws_id = await resolve_workspace_id(session, ctx)
    amount_col = func.coalesce(Transaction.amount_primary, Transaction.amount)

    # Group expression + label expression.
    if group_by == "category":
        bucket_id = Transaction.category_id
        label_q = select(Category.id, Category.name).where(Category.workspace_id == ws_id)
    elif group_by == "account":
        bucket_id = Transaction.account_id
        label_q = None
    elif group_by == "payee":
        bucket_id = Transaction.payee_id
        label_q = None
    elif group_by == "month":
        bucket_id = func.to_char(Transaction.date, "YYYY-MM")
        label_q = None
    elif group_by == "day":
        bucket_id = func.to_char(Transaction.date, "YYYY-MM-DD")
        label_q = None
    else:
        return {"error": f"unknown group_by: {group_by}"}

    if metric == "sum":
        value_expr = func.sum(amount_col)
    elif metric == "avg":
        value_expr = func.avg(amount_col)
    else:  # count
        value_expr = func.count(Transaction.id)

    q = (
        select(bucket_id.label("bucket"), value_expr.label("value"), func.count(Transaction.id).label("count"))
        .where(Transaction.workspace_id == ws_id)
        # Opening-balance rows are bookkeeping, not money the user moved;
        # they are also uncategorized, so "how much is uncategorized?"
        # summed an account's entire starting balance into the answer.
        .where(Transaction.source != "opening_balance")
    )

    fd = parse_date(from_date)
    td = parse_date(to_date)
    if fd:
        q = q.where(Transaction.date >= fd)
    if td:
        q = q.where(Transaction.date <= td)

    accs = parse_uuid_list(account_ids)
    if accs:
        q = q.where(Transaction.account_id.in_(accs))
    cats, cat_error = await resolve_categories(session, ws_id, ids=category_ids, names=category_names)
    if cat_error:
        return cat_error
    if cats:
        q = q.where(Transaction.category_id.in_(cats))
    if uncategorized:
        q = q.where(Transaction.category_id.is_(None))
    if payee_id:
        # parse_uuid_list returns a list — single payee_id wrapped works too.
        pids = parse_uuid_list([payee_id])
        if pids:
            q = q.where(Transaction.payee_id == pids[0])
    if currency:
        q = q.where(Transaction.currency == currency.upper())
    if description_contains:
        q = q.where(Transaction.description.ilike(f"%{description_contains}%"))
    # Status: default to "posted" so totals reflect money that actually
    # moved. The model can opt into "pending" or "all" when the user
    # explicitly asks about scheduled/projected amounts.
    if status == "posted":
        q = q.where(Transaction.status == "posted")
    elif status == "pending":
        q = q.where(Transaction.status == "pending")
    # status == "all" → no filter

    # Securo convention: type='debit' = expense, type='credit' = income.
    # Amount sign isn't authoritative because some imports normalize it.
    if tx_type == "expense":
        q = q.where(Transaction.type == "debit")
    elif tx_type == "income":
        q = q.where(Transaction.type == "credit")

    if exclude_transfers:
        q = q.where(Transaction.transfer_pair_id.is_(None))

    q = q.group_by(bucket_id).order_by(value_expr.desc().nulls_last()).limit(int(limit))

    rows = (await session.execute(q)).mappings().all()
    label_map: dict[Any, str] = {}
    if label_q is not None:
        for cid, cname in (await session.execute(label_q)).all():
            label_map[cid] = cname

    items = [
        {
            "bucket": str(r["bucket"]) if r["bucket"] is not None else None,
            "label": label_map.get(r["bucket"]),
            "value": num(r["value"]),
            "count": int(r.get("count", 0)),
        }
        for r in rows
    ]
    out: dict[str, Any] = {
        "metric": metric,
        "group_by": group_by,
        "from_date": fd.isoformat() if fd else None,
        "to_date": td.isoformat() if td else None,
        "items": items,
        "total": len(items),
        # Across every bucket, so the model never has to add buckets by
        # hand (it reliably reports one bucket as the whole when a search
        # splits across two payees). For avg this is the overall count only.
        "grand_count": sum(int(i.get("count") or 0) for i in items),
    }
    if metric in ("sum", "count"):
        out["grand_total"] = round(sum(float(i.get("value") or 0) for i in items), 2)
    if not items and description_contains and not cats:
        # The classic miss: the model typed a category name into the text
        # filter. Say so, with the id, so the retry is a one-liner.
        named, _ = await resolve_categories(session, ws_id, names=[description_contains])
        if named:
            out["hint"] = (
                f"No transaction text contains {description_contains!r}, but a category with "
                f"that name exists (id {named[0]}). Pass category_ids=[that id] or "
                "category_names=[the name] to total by category."
            )
    return out
