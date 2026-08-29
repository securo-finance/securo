import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.transaction import TransactionRead
from app.services import reconciliation_service
from app.services.reconciliation_service import ReconciliationSuggestion

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])


class ApplyReconciliationRequest(BaseModel):
    transaction_id: uuid.UUID
    match_type: str
    expected_id: uuid.UUID


@router.get("/suggestions", response_model=List[ReconciliationSuggestion])
async def get_suggestions(
    account_ids: Optional[List[uuid.UUID]] = Query(None),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    suggestions = await reconciliation_service.get_reconciliation_suggestions(
        session,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user_id,
        account_ids=account_ids,
        from_date=from_date,
        to_date=to_date,
    )
    return suggestions


@router.post("/apply", response_model=TransactionRead)
async def apply_reconciliation(
    req: ApplyReconciliationRequest,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_write()
    try:
        tx = await reconciliation_service.apply_reconciliation(
            session,
            workspace_id=ctx.workspace.id,
            transaction_id=req.transaction_id,
            match_type=req.match_type,
            expected_id=req.expected_id,
        )
        return TransactionRead.model_validate(tx, from_attributes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
