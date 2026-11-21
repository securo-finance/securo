"""User lookup for group invites.

Two modes:
- ``/lookup`` — exact-match by email, used to validate a typed address
  before creating a member.
- ``/directory`` — the caller and the users who share a workspace with
  them, used to power the "pick an existing user" dropdown when adding a
  member.

The directory used to list every account on the instance, which handed any
signed-in user the email and id of everyone else (#1173). Someone outside
the caller's workspaces is still reachable by typing their exact address,
which ``/lookup`` resolves.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.services import workspace_service

router = APIRouter(prefix="/api/users", tags=["users"])


class UserLookupResult(BaseModel):
    id: uuid.UUID
    email: EmailStr
    # The name the person set for themselves, so a form that links them can
    # show it instead of guessing one from the address.
    display_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


@router.get("/lookup", response_model=UserLookupResult)
async def lookup_user_by_email(
    email: EmailStr = Query(..., description="Exact email to look up"),
    session: AsyncSession = Depends(get_async_session),
    _: User = Depends(current_active_user),
):
    result = await session.execute(
        select(User).where(func.lower(User.email) == email.lower())
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/directory", response_model=list[UserLookupResult])
async def list_users_directory(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """The caller and everyone who shares a workspace with them."""
    return await workspace_service.list_directory_users(session, user.id)
