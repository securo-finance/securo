from typing import Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.spending_plan import (
    SpendingPlanRead,
    SpendingPlanSimulation,
    SpendingPlanTimeline,
    SpendingPlanWrite,
)
from app.services import spending_plan_service

router = APIRouter(prefix="/api/spending-plan", tags=["spending-plan"])


@router.get("", response_model=Optional[SpendingPlanRead])
async def get_spending_plan(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await spending_plan_service.get_plan(session, ctx.workspace.id)


@router.put("", response_model=SpendingPlanRead)
async def save_spending_plan(
    data: SpendingPlanWrite,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await spending_plan_service.save_plan(session, ctx.workspace.id, data)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_spending_plan(
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    await spending_plan_service.delete_plan(session, ctx.workspace.id)


@router.get("/timeline", response_model=SpendingPlanTimeline)
async def get_spending_plan_timeline(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await spending_plan_service.get_timeline(session, ctx.workspace.id, ctx.user_id)


@router.post("/preview", response_model=SpendingPlanTimeline)
async def preview_spending_plan(
    data: SpendingPlanWrite,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """The timeline for a plan being edited. Saves nothing."""
    return await spending_plan_service.get_timeline(
        session, ctx.workspace.id, ctx.user_id, draft=data
    )


@router.post("/simulate", response_model=SpendingPlanTimeline)
async def simulate_spending_plan(
    data: SpendingPlanSimulation,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """The timeline as it would look after the given purchases. Saves nothing."""
    return await spending_plan_service.get_timeline(
        session, ctx.workspace.id, ctx.user_id, data.daily_allowance, data.purchases
    )
