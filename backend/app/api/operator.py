"""The operator's side of a managed instance (ADR 0049).

Outside the customer's admin on purpose: on a hosted instance the customer's
administrator is a superuser (ADR 0045), and the per-account limits are the
operator's. Guarded by OPERATOR_TOKEN alone; unset, these routes answer 404 as
if they did not exist.
"""
import hmac
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_async_session
from app.models.user import User
from app.models.user_quota import UserQuota
from app.services import notification_service, quota_service

router = APIRouter(prefix="/api/operator", tags=["operator"])


def operator_only(request: Request) -> None:
    expected = get_settings().operator_token.get_secret_value()
    if not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    scheme, _, given = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator token required")


Limit = Optional[int]

# The columns are 32-bit integers; past this the database refuses with a 500.
_INT32_MAX = 2_147_483_647


class QuotaValues(BaseModel):
    """A null counter is unbounded."""

    max_personal_workspaces: Limit = Field(default=None, ge=0, le=_INT32_MAX)
    max_business_workspaces: Limit = Field(default=None, ge=0, le=_INT32_MAX)
    max_bank_connections: Limit = Field(default=None, ge=0, le=_INT32_MAX)
    max_members_per_workspace: Limit = Field(default=None, ge=0, le=_INT32_MAX)


class QuotaRead(QuotaValues):
    user_id: uuid.UUID
    #: After this moment an excess has an effect (ADR 0055); null, never.
    excess_effective_at: Optional[datetime] = None


class ExcessDate(BaseModel):
    """When the account's excess starts to matter; null clears it."""

    effective_at: Optional[datetime] = None


def _read(user_id: uuid.UUID, quota: Optional[UserQuota]) -> QuotaRead:
    values = {c: getattr(quota, c) if quota else None for c in quota_service.COUNTERS}
    moment = quota.excess_effective_at if quota else None
    if moment is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return QuotaRead(user_id=user_id, excess_effective_at=moment, **values)


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
    return _read(user_id, await quota_service.get_quota(session, user_id))


@router.put("/quotas/{user_id}", response_model=QuotaRead, dependencies=[Depends(operator_only)])
async def write_quota(
    user_id: uuid.UUID, body: QuotaValues, session: AsyncSession = Depends(get_async_session)
):
    await _user(session, user_id)
    quota = await quota_service.set_quota(session, user_id, body.model_dump())
    await notification_service.quota_changed(session, user_id)
    await session.commit()
    return _read(user_id, quota)


@router.put(
    "/quotas/{user_id}/excess-date", response_model=QuotaRead, dependencies=[Depends(operator_only)]
)
async def write_excess_date(
    user_id: uuid.UUID, body: ExcessDate, session: AsyncSession = Depends(get_async_session)
):
    """Set when an excess starts to have an effect, and tell the account now (ADR 0055)."""
    await _user(session, user_id)
    quota = await quota_service.set_excess_date(session, user_id, body.effective_at)
    await notification_service.quota_changed(session, user_id)
    await session.commit()
    return _read(user_id, quota)


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
