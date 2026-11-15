from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.forecast import CashflowForecastResponse, RecurringDetectionResponse
from app.services import cashflow_forecast_service, recurring_detector_service

router = APIRouter(prefix="/api/forecast", tags=["forecast"])


@router.get("/recurring", response_model=RecurringDetectionResponse)
async def get_detected_recurring_transactions(
    min_occurrences: int = Query(2, ge=2, le=20, description="Minimum occurrences to classify as recurring"),
    lookback_days: int = Query(365, ge=30, le=730, description="Number of days to analyze historical stream"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Detect recurring expenses, subscriptions, and periodic income streams using interval clustering."""
    return await recurring_detector_service.detect_recurring_patterns(
        session=session,
        workspace_id=ctx.workspace.id,
        min_occurrences=min_occurrences,
        lookback_days=lookback_days,
    )


@router.get("/cashflow", response_model=CashflowForecastResponse)
async def get_cashflow_forecast(
    horizon_days: int = Query(90, ge=7, le=365, description="Forecast horizon in days (e.g. 30, 60, 90)"),
    include_discretionary: bool = Query(True, description="Include 90-day daily discretionary baseline burn"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Generate day-by-day predictive cashflow simulation and calculate liquidity runway."""
    return await cashflow_forecast_service.generate_cashflow_forecast(
        session=session,
        workspace_id=ctx.workspace.id,
        horizon_days=horizon_days,
        include_discretionary_burn=include_discretionary,
    )
