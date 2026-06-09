import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.dashboard import DashboardSummary, SpendingByCategory, MonthlyTrend, ProjectedTransaction, BalanceHistory
from app.services import dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(
    month: Optional[date] = Query(None),
    balance_date: Optional[date] = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    asset_group_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await dashboard_service.get_summary(
        session, ctx.workspace.id, ctx.user_id, month, balance_date, account_ids,
        asset_group_ids,
    )


@router.get("/spending-by-category", response_model=list[SpendingByCategory])
async def get_spending_by_category(
    month: Optional[date] = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await dashboard_service.get_spending_by_category(
        session, ctx.workspace.id, ctx.user_id, month, account_ids
    )


@router.get("/monthly-trend", response_model=list[MonthlyTrend])
async def get_monthly_trend(
    months: int = Query(6, ge=1, le=12),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await dashboard_service.get_monthly_trend(
        session, ctx.workspace.id, ctx.user_id, months, account_ids
    )


@router.get("/balance-history", response_model=BalanceHistory)
async def get_balance_history(
    month: Optional[date] = Query(None),
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await dashboard_service.get_balance_history(
        session, ctx.workspace.id, ctx.user_id, month, account_ids
    )


@router.get("/projected-transactions", response_model=list[ProjectedTransaction])
async def get_projected_transactions(
    month: Optional[date] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await dashboard_service.get_projected_transactions(
        session, ctx.workspace.id, ctx.user_id, month
    )
