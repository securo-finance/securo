import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.reimbursement import (
    ReimbursementAllocationsInput,
    ReimbursementLinkedTransaction,
    TransactionReimbursementsRead,
)
from app.services import reimbursement_service, transaction_service

# Nested under transactions: a link belongs to the rows it joins, and access
# tokens reach it through the `transactions` scope.
router = APIRouter(prefix="/api/transactions", tags=["reimbursements"])


async def _transaction(
    session: AsyncSession, transaction_id: uuid.UUID, ctx: WorkspaceContext, *, for_update: bool = False
):
    tx = await transaction_service.get_transaction(
        session, transaction_id, ctx.workspace.id, for_update=for_update
    )
    if not tx:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    return tx


@router.get("/{transaction_id}/reimbursements", response_model=TransactionReimbursementsRead)
async def get_reimbursements(
    transaction_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    tx = await _transaction(session, transaction_id, ctx)
    return await reimbursement_service.get_reimbursements(session, tx)


@router.put("/{transaction_id}/reimbursements", response_model=TransactionReimbursementsRead)
async def replace_reimbursements(
    transaction_id: uuid.UUID,
    data: ReimbursementAllocationsInput,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Replace every counterpart of the anchor `transaction_id`."""
    # Held until the commit below: two saves of one anchor then queue, and the
    # second validates against what the first committed instead of both
    # clearing and inserting on the same empty set.
    tx = await _transaction(session, transaction_id, ctx, for_update=True)
    try:
        await reimbursement_service.replace_allocations(session, tx, data.allocations)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    await session.commit()
    return await reimbursement_service.get_reimbursements(session, tx)


@router.get(
    "/{transaction_id}/reimbursement-candidates",
    response_model=list[ReimbursementLinkedTransaction],
)
async def get_reimbursement_candidates(
    transaction_id: uuid.UUID,
    looking_for: Literal["counterparts", "anchors"] = Query("counterparts"),
    q: Optional[str] = Query(None, max_length=200),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    limit: int = Query(20, ge=1, le=100),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Rows that could be linked to `transaction_id`: ones that could pay it
    back (`counterparts`) or ones it could pay back (`anchors`)."""
    tx = await _transaction(session, transaction_id, ctx)
    return await reimbursement_service.find_candidates(
        session, tx, looking_for=looking_for, search=q or None,
        from_date=from_date, to_date=to_date, limit=limit,
    )
