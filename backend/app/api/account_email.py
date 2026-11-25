"""Accept or decline a change of login address an administrator asked for.

The address on an account is a recovery credential, so changing it belongs to
the person it authenticates (ADR 0042, #556): the admin route only records the
request, and these two are how its owner ends it — from the session they
signed in with, which is a credential no administrator holds.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.user import UserRead
from app.services import account_email_service

router = APIRouter(prefix="/api/users/me", tags=["users"])


@router.post("/email-change", response_model=UserRead)
async def confirm_email_change(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Move the account to the address an administrator asked for."""
    try:
        updated = await account_email_service.confirm_pending_email(session, user)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return UserRead.model_validate(updated)


@router.delete("/email-change", status_code=status.HTTP_204_NO_CONTENT)
async def decline_email_change(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Keep the current address; the request is dropped."""
    await account_email_service.cancel_pending_email(session, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
