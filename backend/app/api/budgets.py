import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.budget import (
    BudgetBatchCreate,
    BudgetCopyMonthRequest,
    BudgetCreate,
    BudgetForecastResponse,
    BudgetRead,
    BudgetRolloverSummaryResponse,
    BudgetSuggestionResponse,
    BudgetUpdate,
    BudgetVsActual,
    ExpectedIncomeCreate,
    ExpectedIncomeRead,
)
from app.services import budget_service

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetRead])
async def list_budgets(
    month: Optional[date] = Query(None),
    primary_month: Optional[date] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Budgets a month resolves to, with `amount_primary` in the viewer's currency.

    `primary_month` converts `amount_primary` at that month's rate instead of
    `month`'s, so a copy preview can show a source limit at the rate of the
    month it is being copied into.
    """
    budgets = await budget_service.get_budgets(session, ctx.workspace.id, month)
    return await budget_service.budget_reads(
        session, budgets, ctx.user_id, primary_month or month
    )


@router.post("", response_model=BudgetRead, status_code=status.HTTP_201_CREATED)
async def create_budget(
    data: BudgetCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        budget = await budget_service.create_budget(session, ctx.workspace.id, ctx.user_id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    [read] = await budget_service.budget_reads(session, [budget], ctx.user_id)
    return read


@router.post(
    "/batch", response_model=list[BudgetRead], status_code=status.HTTP_201_CREATED
)
async def create_budgets_batch(
    data: BudgetBatchCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        budgets = await budget_service.create_budgets_batch(
            session, ctx.workspace.id, ctx.user_id, data
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return await budget_service.budget_reads(session, budgets, ctx.user_id)


@router.get("/suggestions", response_model=BudgetSuggestionResponse)
async def budget_suggestions(
    month: Optional[date] = Query(None),
    months: int = Query(3, ge=1, le=24),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await budget_service.get_budget_suggestions(
        session, ctx.workspace.id, ctx.user_id, month, months
    )


@router.patch("/{budget_id}", response_model=BudgetRead)
async def update_budget(
    budget_id: uuid.UUID,
    data: BudgetUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    budget = await budget_service.update_budget(
        session, budget_id, ctx.workspace.id, data, ctx.user_id
    )
    if not budget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    [read] = await budget_service.budget_reads(session, [budget], ctx.user_id)
    return read


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    deleted = await budget_service.delete_budget(session, budget_id, ctx.workspace.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")


@router.get("/comparison", response_model=list[BudgetVsActual])
async def budget_comparison(
    month: Optional[date] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await budget_service.get_budget_vs_actual(session, ctx.workspace.id, ctx.user_id, month)


@router.post("/copy-month", response_model=list[BudgetRead])
async def copy_budgets_endpoint(
    req: BudgetCopyMonthRequest,
    db: AsyncSession = Depends(get_async_session),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
):
    """Copy all budget category allocations from a source month to a target month with optional percentage adjustments."""
    try:
        budgets = await budget_service.copy_monthly_budgets(
            db=db,
            workspace_id=ctx.workspace.id,
            user_id=ctx.user_id,
            source_month=req.source_month,
            target_month=req.target_month,
            adjustment_percentage=req.adjustment_percentage,
            overwrite_existing=req.overwrite_existing,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return await budget_service.budget_reads(db, budgets, ctx.user_id)


@router.get("/rollover-summary", response_model=BudgetRolloverSummaryResponse)
async def get_rollover_summary_endpoint(
    month: date = Query(...),
    db: AsyncSession = Depends(get_async_session),
    ctx: WorkspaceContext = Depends(current_workspace),
):
    """Calculate end-of-month surpluses and deficits for budget carryover analysis."""
    return await budget_service.get_budget_rollover_summary(
        db=db,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user_id,
        month=month,
    )


@router.get("/forecast", response_model=BudgetForecastResponse)
async def get_budget_forecast_endpoint(
    start_month: date = Query(...),
    months: int = Query(6, ge=1, le=24),
    db: AsyncSession = Depends(get_async_session),
    ctx: WorkspaceContext = Depends(current_workspace),
):
    """Project multi-month budget performance and variance."""
    return await budget_service.get_multi_month_forecast(
        db=db,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user_id,
        start_month=start_month,
        num_months=months,
    )


@router.get("/expected-income", response_model=Optional[ExpectedIncomeRead])
async def get_expected_income_endpoint(
    month: Optional[date] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """The month's income typed by hand, read in the reader's display currency."""
    return await budget_service.get_expected_income(
        session,
        ctx.workspace.id,
        month or app_today(),
        ctx.user.primary_currency,
    )


@router.put("/expected-income", response_model=ExpectedIncomeRead)
async def put_expected_income_endpoint(
    data: ExpectedIncomeCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Save the month's income typed by hand, or clear it with a null amount."""
    return await budget_service.set_expected_income(
        session,
        ctx.workspace.id,
        data.month,
        data.amount,
        data.currency or ctx.user.primary_currency,
        reader_currency=ctx.user.primary_currency,
    )
