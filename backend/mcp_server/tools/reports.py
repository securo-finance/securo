from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.services import dashboard_service, report_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import parse_date, resolve_workspace_id


def _pri_currency(ctx: CallContext) -> str:
    # The user model defaults to USD; reports reach into the DB for the
    # actual primary currency, so the value passed here is just the fallback.
    return "USD"


def _serialize_report(r: Any) -> dict[str, Any]:
    if hasattr(r, "model_dump"):
        r = r.model_dump(mode="json")
    return _slim_report(r) if isinstance(r, dict) else {"value": str(r)}


_MAX_TREND_POINTS = 60


def _slim_report(r: dict[str, Any]) -> dict[str, Any]:
    """Keep what a model can use; drop what only a chart can.

    The report endpoints return, for every trend point, a per-account
    ``breakdowns`` map and a full ``composition`` list. A month of daily
    net worth is ~50 KB (~28k tokens); daily income/expenses is ~100 KB.
    The model only ever quotes the summary and the series, so keep
    ``summary``, the top-level ``composition`` and ``meta``, and reduce each
    point to date/value/change — the latest point keeps its breakdowns so
    "what is it made of?" still has an answer. Long series are sampled down
    to ``_MAX_TREND_POINTS`` (first and last always kept) and say so.
    """
    trend = r.get("trend")
    if not isinstance(trend, list):
        return r
    points = [p for p in trend if isinstance(p, dict)]
    dropped = 0
    if len(points) > _MAX_TREND_POINTS:
        step = (len(points) - 1) / (_MAX_TREND_POINTS - 1)
        keep = sorted({round(i * step) for i in range(_MAX_TREND_POINTS)} | {0, len(points) - 1})
        dropped = len(points) - len(keep)
        points = [points[i] for i in keep]
    slim = []
    for i, p in enumerate(points):
        q = {k: p.get(k) for k in ("date", "value", "change") if k in p}
        if i == len(points) - 1 and isinstance(p.get("breakdowns"), dict):
            q["breakdowns"] = p["breakdowns"]
        slim.append(q)
    out = {k: v for k, v in r.items() if k != "trend"}
    out["trend"] = slim
    if dropped:
        out["trend_note"] = f"{dropped} intermediate points omitted; {len(slim)} evenly spaced points kept."
    return out


@tool(
    name="get_net_worth",
    description="Net worth time series over the last N months. Use to answer 'how is my net worth trending?'",
    parameters={
        "type": "object",
        "properties": {
            "months": {"type": "integer", "minimum": 1, "maximum": 60, "default": 12},
            "interval": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"], "default": "monthly"},
        },
        "additionalProperties": False,
    },
    tags=["read", "reports"],
)
async def get_net_worth(
    *,
    session: AsyncSession,
    ctx: CallContext,
    months: int = 12,
    interval: str = "monthly",
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rep = await report_service.get_net_worth_report(
        session, ws_id, ctx.user_id, months=int(months), interval=interval, currency=_pri_currency(ctx)
    )
    return _serialize_report(rep)


@tool(
    name="get_income_expenses",
    description="Income vs expenses over the last N months. Use for budgeting questions.",
    parameters={
        "type": "object",
        "properties": {
            "months": {"type": "integer", "minimum": 1, "maximum": 60, "default": 12},
            "interval": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"], "default": "monthly"},
        },
        "additionalProperties": False,
    },
    tags=["read", "reports"],
)
async def get_income_expenses(
    *,
    session: AsyncSession,
    ctx: CallContext,
    months: int = 12,
    interval: str = "monthly",
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rep = await report_service.get_income_expenses_report(
        session, ws_id, ctx.user_id, months=int(months), interval=interval, currency=_pri_currency(ctx)
    )
    return _serialize_report(rep)


@tool(
    name="get_cash_flow",
    description="Forward-looking cash flow projection — current balance plus future bookings and recurring transactions.",
    parameters={
        "type": "object",
        "properties": {
            "months": {"type": "integer", "minimum": 1, "maximum": 24, "default": 6},
            "interval": {"type": "string", "enum": ["daily", "weekly", "monthly"], "default": "daily"},
        },
        "additionalProperties": False,
    },
    tags=["read", "reports"],
)
async def get_cash_flow(
    *,
    session: AsyncSession,
    ctx: CallContext,
    months: int = 6,
    interval: str = "daily",
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    rep = await report_service.get_cash_flow_report(
        session, ws_id, ctx.user_id, months=int(months), interval=interval, currency=_pri_currency(ctx)
    )
    return _serialize_report(rep)


@tool(
    name="get_dashboard_snapshot",
    description=(
        "One-call month snapshot: total income/expenses/savings, balances by "
        "currency, top categories. Defaults to the current month."
    ),
    parameters={
        "type": "object",
        "properties": {
            "month": {"type": "string", "format": "date", "description": "Any date inside the target month"},
        },
        "additionalProperties": False,
    },
    tags=["read", "dashboard"],
)
async def get_dashboard_snapshot(
    *,
    session: AsyncSession,
    ctx: CallContext,
    month: str | None = None,
) -> dict[str, Any]:
    target = parse_date(month) or app_today().replace(day=1)
    ws_id = await resolve_workspace_id(session, ctx)
    summary = await dashboard_service.get_summary(session, ws_id, ctx.user_id, month=target)
    if hasattr(summary, "model_dump"):
        return summary.model_dump(mode="json")
    return {"value": str(summary)}
