from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.month import (
    CloseCurrentMonthRead,
    CloseCurrentMonthRequest,
    CurrentMonthRead,
    CurrentMonthUpdate,
    CurrentMonthViewUpdate,
)
from app.services import month_service

router = APIRouter(prefix="/api/months", tags=["months"])


@router.get("/current", response_model=CurrentMonthRead)
async def get_current_month(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    return await month_service.get_current_month_state(session, user)


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


@router.put("/view", response_model=CurrentMonthRead)
async def set_month_view(
    data: CurrentMonthViewUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        return await month_service.set_month_view(
            session,
            user.id,
            user.preferences,
            data.mode,
            data.period,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/close", response_model=CloseCurrentMonthRead)
async def close_current_month(
    data: CloseCurrentMonthRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        return await month_service.close_current_month(
            session,
            user.id,
            user,
            data.next_period,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
