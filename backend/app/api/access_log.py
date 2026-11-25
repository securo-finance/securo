from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.admin import AdminAccessLogList, AdminAccessLogRead
from app.services import admin_service

router = APIRouter(prefix="/api/access-log", tags=["access-log"])


@router.get("/mine", response_model=AdminAccessLogList)
async def my_access_log(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Administrative actions on my account, and on members of workspaces I own.

    Whoever could have been impersonated through an account gets to see that
    an administrator touched it, with the reason and the date (ADR 0042).
    """
    entries, total = await admin_service.list_access_log_for_viewer(
        session, user.id, page=page, limit=limit
    )
    return AdminAccessLogList(
        items=[AdminAccessLogRead.model_validate(e) for e in entries],
        total=total,
    )
