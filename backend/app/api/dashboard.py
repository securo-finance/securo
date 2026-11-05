import json
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import current_active_user
from app.core.database import async_session_maker, get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.models.user import User
from app.schemas.dashboard import DashboardSummary, SpendingByCategory, MonthlyTrend, ProjectedTransaction, BalanceHistory
from app.schemas.dashboard import CommittedDashboardLayout, DashboardLayoutWrite
from app.services import dashboard_service
from app.services.dashboard_layout_preferences import save_dashboard_layout

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

LAYOUT_BODY_LIMIT = 16_384


def get_dashboard_layout_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_session_maker


@router.put("/layouts/{workspace_id}", response_model=CommittedDashboardLayout)
async def put_dashboard_layout(
    workspace_id: str,
    request: Request,
    user: User = Depends(current_active_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_dashboard_layout_session_factory
    ),
):
    try:
        target = uuid.UUID(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid workspace UUID") from exc
    if str(target) != workspace_id:
        raise HTTPException(status_code=422, detail="Workspace UUID must be canonical")
    header = request.headers.get("X-Workspace-Id")
    if header is not None:
        try:
            header_target = uuid.UUID(header)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="WORKSPACE_HEADER_CONFLICT") from exc
        if header_target != target:
            raise HTTPException(status_code=409, detail="WORKSPACE_HEADER_CONFLICT")

    # No automatic body-model injection: Content-Length is not trusted, and
    # even an oversized ASGI chunk never enlarges this buffer past limit + 1.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk[:LAYOUT_BODY_LIMIT + 1 - len(raw)])
        if len(raw) > LAYOUT_BODY_LIMIT:
            raise HTTPException(status_code=413, detail="Dashboard layout body too large")
    try:
        body = DashboardLayoutWrite.model_validate(json.loads(raw))
    except (ValueError, UnicodeDecodeError, RecursionError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Invalid dashboard layout") from exc
    if body.workspaceId != workspace_id:
        raise HTTPException(status_code=422, detail="workspaceId must match the path")
    return await save_dashboard_layout(
        actor_id=user.id, workspace_id=target, layout=body, session_factory=session_factory
    )


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
    comparison_months: Optional[list[date]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
) -> BalanceHistory:
    try:
        return await dashboard_service.get_balance_history(
            session, ctx.workspace.id, ctx.user_id, month, account_ids, comparison_months
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/balance-history/months", response_model=list[str])
async def get_balance_history_months(
    account_ids: Optional[list[uuid.UUID]] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await dashboard_service.get_balance_history_months(
        session, ctx.workspace.id, account_ids
    )


@router.get("/projected-transactions", response_model=list[ProjectedTransaction])
async def get_projected_transactions(
    month: Optional[date] = Query(None),
    account_id: Optional[uuid.UUID] = Query(None),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await dashboard_service.get_projected_transactions(
            session,
            ctx.workspace.id,
            ctx.user_id,
            month,
            account_id=account_id,
            from_date=from_date,
            to_date=to_date,
        )
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=str(exc)) from exc
