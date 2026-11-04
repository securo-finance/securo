import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.payee import PayeeCreate, PayeeMergeRequest, PayeeRead, PayeeSummary, PayeeUpdate, PayeeBulkDeleteRequest
from app.schemas.category import CategoryRead
from app.services import payee_service

router = APIRouter(prefix="/api/payees", tags=["payees"])


def _payee_in_use(error: ValueError) -> HTTPException:
    """Turn "this counterparty is still referenced" into an answer.

    Invoices hold their payee under a RESTRICT foreign key on purpose:
    deleting a client must never silently delete the record of money they
    owed. Without this the constraint surfaces as a 500, which tells the
    user nothing and tells the operator that something crashed.

    409 rather than 400: the request is well formed and the refusal is
    about the state of the data, which is exactly what Conflict means.
    """
    reason = str(error)
    if reason.startswith("payee_has_invoices:"):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "payee_has_invoices",
                "count": int(reason.split(":", 1)[1] or 0),
                # The way out, named: merging keeps the history, which is
                # what someone deleting a duplicate actually wants.
                "message": "This client has invoices. Merge it into another client instead.",
            },
        )
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)


@router.get("", response_model=list[PayeeRead])
async def list_payees(
    q: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    is_favorite: Optional[bool] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await payee_service.get_payees(
        session, ctx.workspace.id, q=q, type=type, is_favorite=is_favorite
    )


@router.get("/{payee_id}", response_model=PayeeRead)
async def get_payee(
    payee_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    payee = await payee_service.get_payee(session, payee_id, ctx.workspace.id)
    if not payee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payee not found")
    return payee


@router.get("/{payee_id}/summary", response_model=PayeeSummary)
async def get_payee_summary(
    payee_id: uuid.UUID,
    start_date: Optional[date] = Query(None, alias="from"),
    end_date: Optional[date] = Query(None, alias="to"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        result = await payee_service.get_payee_summary(session, payee_id, ctx.workspace.id, start_date, end_date)
        return PayeeSummary(
            payee=PayeeRead.model_validate(result["payee"], from_attributes=True),
            total_spent=result["total_spent"],
            total_received=result["total_received"],
            transaction_count=result["transaction_count"],
            most_common_category=CategoryRead.model_validate(result["most_common_category"], from_attributes=True) if result["most_common_category"] else None,
            last_transaction_date=result["last_transaction_date"],
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("", response_model=PayeeRead, status_code=status.HTTP_201_CREATED)
async def create_payee(
    data: PayeeCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await payee_service.create_payee(session, ctx.workspace.id, ctx.user_id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.patch("/{payee_id}", response_model=PayeeRead)
async def update_payee(
    payee_id: uuid.UUID,
    data: PayeeUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        payee = await payee_service.update_payee(session, payee_id, ctx.workspace.id, data)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if not payee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payee not found")
    return payee


@router.delete("/{payee_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_payee(
    payee_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        deleted = await payee_service.delete_payee(session, payee_id, ctx.workspace.id)
    except ValueError as exc:
        raise _payee_in_use(exc)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payee not found")


@router.post("/merge")
async def merge_payees(
    data: PayeeMergeRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        reassigned = await payee_service.merge_payees(session, ctx.workspace.id, data.target_id, data.source_ids)
        return {"merged": len(data.source_ids), "transactions_reassigned": reassigned}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/bulk-delete", status_code=status.HTTP_200_OK)
async def bulk_delete_payees(
    data: PayeeBulkDeleteRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        deleted_count = await payee_service.bulk_delete_payees(
            session, ctx.workspace.id, data.ids
        )
    except ValueError as exc:
        raise _payee_in_use(exc)
    return {"deleted": deleted_count}
