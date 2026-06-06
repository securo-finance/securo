import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.installment import (
    InstallmentPurchaseDetail,
    InstallmentPurchaseRead,
    InstallmentSummary,
    ManualInstallmentCreate,
    ManualInstallmentUpdate,
    MarkInstallmentPaid,
)
from app.services import installment_service

router = APIRouter(prefix="/api/installments", tags=["installments"])


@router.get("/summary", response_model=InstallmentSummary)
async def get_installments_summary(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await installment_service.get_summary(session, ctx.workspace.id)


@router.get("/purchases", response_model=list[InstallmentPurchaseRead])
async def list_installment_purchases(
    status_filter: str | None = Query(None, alias="status"),
    account_id: uuid.UUID | None = None,
    sort: str = "date",
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await installment_service.get_purchases(
        session, ctx.workspace.id, status_filter, account_id, sort
    )


@router.get("/purchases/{purchase_id}/details", response_model=InstallmentPurchaseDetail)
async def get_installment_purchase_details(
    purchase_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    details = await installment_service.get_purchase_details(
        session, ctx.workspace.id, purchase_id
    )
    if not details:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Purchase not found"
        )
    return details


@router.post(
    "/purchases",
    response_model=InstallmentPurchaseRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_manual_installment(
    data: ManualInstallmentCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await installment_service.create_manual_installment(
            session, ctx.workspace.id, ctx.user_id, data
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )


@router.patch("/purchases/{purchase_id}", response_model=InstallmentPurchaseRead)
async def update_manual_installment(
    purchase_id: uuid.UUID,
    data: ManualInstallmentUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        result = await installment_service.update_manual_installment(
            session, ctx.workspace.id, purchase_id, data
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Purchase not found"
        )
    return result


@router.delete(
    "/purchases/{purchase_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_manual_installment(
    purchase_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        deleted = await installment_service.delete_manual_installment(
            session, ctx.workspace.id, purchase_id
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Purchase not found"
        )


@router.post("/purchases/{purchase_id}/pay", response_model=InstallmentPurchaseRead)
async def mark_installment_paid(
    purchase_id: uuid.UUID,
    data: MarkInstallmentPaid,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        result = await installment_service.mark_installment_paid(
            session,
            ctx.workspace.id,
            purchase_id,
            data.installment_number,
            data.amount,
            data.date,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Purchase not found"
        )
    return result
