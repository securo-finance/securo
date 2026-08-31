import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.debt import (
    DebtCreate,
    DebtInstallmentPay,
    DebtInstallmentRead,
    DebtPayoffProjection,
    DebtPlanCreate,
    DebtPlanRead,
    DebtRead,
    DebtStrategySettingRead,
    DebtStrategySettingUpdate,
    DebtUpdate,
)
from app.services import debt_installment_service, debt_payoff_strategy_service, debt_service

router = APIRouter(prefix="/api/debts", tags=["debts"])


@router.get("", response_model=list[DebtRead])
async def list_debts(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await debt_service.get_debts(session, ctx.workspace.id)


@router.post("", response_model=DebtRead, status_code=status.HTTP_201_CREATED)
async def create_debt(
    data: DebtCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await debt_service.create_debt(session, ctx.workspace.id, ctx.user_id, data)


@router.get("/payoff-projection", response_model=DebtPayoffProjection)
async def get_payoff_projection(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await debt_payoff_strategy_service.compute_payoff_projection(session, ctx.workspace.id)


@router.get("/strategy-setting", response_model=DebtStrategySettingRead)
async def get_strategy_setting(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await debt_payoff_strategy_service.get_or_create_strategy_setting(session, ctx.workspace.id)


@router.patch("/strategy-setting", response_model=DebtStrategySettingRead)
async def update_strategy_setting(
    data: DebtStrategySettingUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await debt_payoff_strategy_service.update_strategy_setting(session, ctx.workspace.id, data)


@router.get("/{debt_id}", response_model=DebtRead)
async def get_debt(
    debt_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    debt = await debt_service.get_debt(session, debt_id, ctx.workspace.id)
    if not debt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debt not found")
    return debt


@router.patch("/{debt_id}", response_model=DebtRead)
async def update_debt(
    debt_id: uuid.UUID,
    data: DebtUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    debt = await debt_service.update_debt(session, debt_id, ctx.workspace.id, data)
    if not debt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debt not found")
    return debt


@router.delete("/{debt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_debt(
    debt_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    deleted = await debt_service.delete_debt(session, debt_id, ctx.workspace.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debt not found")


@router.post("/{debt_id}/plans", response_model=DebtPlanRead, status_code=status.HTTP_201_CREATED)
async def create_debt_plan(
    debt_id: uuid.UUID,
    data: DebtPlanCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        plan = await debt_service.create_debt_plan(session, debt_id, ctx.workspace.id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debt not found")
    return plan


@router.post("/installments/{installment_id}/pay", response_model=DebtInstallmentRead)
async def pay_installment(
    installment_id: uuid.UUID,
    data: DebtInstallmentPay,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        installment = await debt_installment_service.mark_installment_paid(
            session, installment_id, ctx.workspace.id, data
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not installment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Installment not found")
    return installment
