from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.budget import BudgetVsActual
from app.services import budget_service

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


@router.get("/comparison", response_model=list[BudgetVsActual])
async def budget_comparison(
    month: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    return await budget_service.get_budget_vs_actual(session, user.id, month)
