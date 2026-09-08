from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import cashflow_forecast_service, recurring_detector_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_workspace_id


def _serialize_forecast(r: Any) -> dict[str, Any]:
    if hasattr(r, "model_dump"):
        return r.model_dump(mode="json")
    return r if isinstance(r, dict) else {"value": str(r)}


@tool(
    name="get_cashflow_forecast",
    description="Simulates day-by-day predictive cashflow and calculates liquidity runway and shortfall risk over 30, 60, or 90 days.",
    parameters={
        "type": "object",
        "properties": {
            "horizon_days": {"type": "integer", "minimum": 7, "maximum": 365, "default": 90, "description": "Forecast horizon in days (e.g. 30, 60, 90)"},
            "include_discretionary": {"type": "boolean", "default": True, "description": "Whether to include 90-day average baseline discretionary burn"},
        },
        "additionalProperties": False,
    },
    tags=["read", "forecast", "reports"],
)
async def get_cashflow_forecast(
    *,
    session: AsyncSession,
    ctx: CallContext,
    horizon_days: int = 90,
    include_discretionary: bool = True,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    forecast = await cashflow_forecast_service.generate_cashflow_forecast(
        session=session,
        workspace_id=ws_id,
        horizon_days=int(horizon_days),
        include_discretionary_burn=include_discretionary,
    )
    return _serialize_forecast(forecast)


@tool(
    name="get_recurring_subscriptions",
    description="Detects all recurring expenses, active subscriptions, and predictable periodic income streams from historical transactions.",
    parameters={
        "type": "object",
        "properties": {
            "min_occurrences": {"type": "integer", "minimum": 2, "maximum": 20, "default": 2, "description": "Minimum transaction occurrences to classify as recurring"},
            "lookback_days": {"type": "integer", "minimum": 30, "maximum": 730, "default": 365, "description": "Lookback window in days"},
        },
        "additionalProperties": False,
    },
    tags=["read", "forecast", "subscriptions"],
)
async def get_recurring_subscriptions(
    *,
    session: AsyncSession,
    ctx: CallContext,
    min_occurrences: int = 2,
    lookback_days: int = 365,
) -> dict[str, Any]:
    ws_id = await resolve_workspace_id(session, ctx)
    recurring = await recurring_detector_service.detect_recurring_patterns(
        session=session,
        workspace_id=ws_id,
        min_occurrences=int(min_occurrences),
        lookback_days=int(lookback_days),
    )
    return _serialize_forecast(recurring)
