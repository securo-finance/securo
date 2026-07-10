"""User lookup for group invites.

Two modes:
- ``/lookup`` — exact-match by email, used to validate a typed address
  before creating a member.
- ``/directory`` — flat list of all users on the instance, used to power
  the "pick an existing user" dropdown when adding a member.

The directory endpoint exposes every user's email to any authenticated
caller. That is acceptable for self-hosted, single-tenant deployments
(everyone on the instance trusts each other). It will need to be scoped
to "users in the same organization" once multi-tenancy lands; flag this
when introducing org boundaries.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User

router = APIRouter(prefix="/api/users", tags=["users"])


class UserLookupResult(BaseModel):
    id: uuid.UUID
    email: EmailStr

    model_config = ConfigDict(from_attributes=True)


@router.get("/lookup", response_model=UserLookupResult)
async def lookup_user_by_email(
    email: EmailStr = Query(..., description="Exact email to look up"),
    session: AsyncSession = Depends(get_async_session),
    _: User = Depends(current_active_user),
):
    result = await session.execute(select(User).where(func.lower(User.email) == email.lower()))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/directory", response_model=list[UserLookupResult])
async def list_users_directory(
    session: AsyncSession = Depends(get_async_session),
    _: User = Depends(current_active_user),
):
    """List every user on the instance — for the member-picker dropdown.

    Single-tenant assumption: scope to org once multi-tenancy lands.
    """
    result = await session.execute(select(User).order_by(User.email))
    return list(result.scalars().all())
