from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.month import CurrentMonthRead, CurrentMonthUpdate
from app.services import month_service

router = APIRouter(prefix="/api/months", tags=["months"])


@router.get("/current", response_model=CurrentMonthRead)
async def get_current_month(
    user: User = Depends(current_active_user),
):
    return month_service.get_current_month_state(user)


@router.put("/current", response_model=CurrentMonthRead)
async def set_current_month(
    data: CurrentMonthUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        return await month_service.set_current_month_period(
            session,
            user.id,
            user.preferences,
            data.period,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
