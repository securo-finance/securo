"""The operator's side of a managed instance (ADR 0049).

Outside the customer's admin on purpose: on a hosted instance the customer's
administrator is a superuser (ADR 0045), and the per-account limits are the
operator's. Guarded by OPERATOR_TOKEN alone; unset, these routes answer 404 as
if they did not exist. The account lookup also needs MANAGED_INSTANCE on.
"""
import hmac
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_async_session
from app.models.user import User
from app.services import operator_account_service, quota_service

router = APIRouter(prefix="/api/operator", tags=["operator"])


def operator_only(request: Request) -> None:
    expected = get_settings().operator_token.get_secret_value()
    if not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    scheme, _, given = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator token required")


def managed_operator_only(request: Request) -> None:
    """The operator's token on a managed instance; off, the route does not exist."""
    if not get_settings().managed_instance:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    operator_only(request)


Limit = Optional[int]


class QuotaValues(BaseModel):
    """A null counter is unbounded."""

    max_personal_workspaces: Limit = Field(default=None, ge=0)
    max_business_workspaces: Limit = Field(default=None, ge=0)
    max_bank_connections: Limit = Field(default=None, ge=0)
    max_members_per_workspace: Limit = Field(default=None, ge=0)


class QuotaRead(QuotaValues):
    user_id: uuid.UUID


class CounterFitRead(BaseModel):
    counter: str
    current: int
    target: Limit
    excess: int


class FitCheckRead(BaseModel):
    allowed: bool
    counters: list[CounterFitRead]


async def _user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/quotas/{user_id}", response_model=QuotaRead, dependencies=[Depends(operator_only)])
async def read_quota(user_id: uuid.UUID, session: AsyncSession = Depends(get_async_session)):
    await _user(session, user_id)
    quota = await quota_service.get_quota(session, user_id)
    values = {c: getattr(quota, c) if quota else None for c in quota_service.COUNTERS}
    return QuotaRead(user_id=user_id, **values)


@router.put("/quotas/{user_id}", response_model=QuotaRead, dependencies=[Depends(operator_only)])
async def write_quota(
    user_id: uuid.UUID, body: QuotaValues, session: AsyncSession = Depends(get_async_session)
):
    await _user(session, user_id)
    quota = await quota_service.set_quota(session, user_id, body.model_dump())
    await session.commit()
    return QuotaRead(user_id=user_id, **{c: getattr(quota, c) for c in quota_service.COUNTERS})


@router.post(
    "/quotas/{user_id}/fit-check",
    response_model=FitCheckRead,
    dependencies=[Depends(operator_only)],
)
async def fit_check(
    user_id: uuid.UUID, body: QuotaValues, session: AsyncSession = Depends(get_async_session)
):
    """Whether the account already fits the target limits. Writes nothing."""
    await _user(session, user_id)
    result = await quota_service.check_fit(session, user_id, body.model_dump())
    return FitCheckRead(
        allowed=result.allowed,
        counters=[CounterFitRead(**vars(f)) for f in result.counters],
    )


class OwnedWorkspaceRead(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    is_archived: bool
    members: int


class CounterUsageRead(BaseModel):
    counter: str
    current: int
    maximum: Limit


class AccountRead(BaseModel):
    """Who the account is and what it holds in numbers; nothing from its workspaces."""

    id: uuid.UUID
    email: str
    has_password: bool
    is_active: bool
    workspaces: list[OwnedWorkspaceRead]
    usage: list[CounterUsageRead]


@router.get("/accounts", response_model=AccountRead, dependencies=[Depends(managed_operator_only)])
async def look_up_account(
    user_id: Optional[uuid.UUID] = Query(default=None),
    email: Optional[EmailStr] = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
):
    """One account, by its id or its email. Exactly one of them."""
    if (user_id is None) == (email is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Look the account up by exactly one of user_id or email.",
        )
    user = await operator_account_service.find_account(session, user_id=user_id, email=email)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    view = await operator_account_service.describe(session, user)
    return AccountRead(
        id=user.id,
        email=user.email,
        has_password=operator_account_service.password_is_set(user),
        is_active=user.is_active,
        workspaces=[OwnedWorkspaceRead(**vars(w)) for w in view.workspaces],
        usage=[CounterUsageRead(**vars(u)) for u in view.usage],
    )
