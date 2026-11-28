"""Where the signed-in account stands against its own numbers (ADR 0049, ADR 0055).

Only on a managed instance; elsewhere it answers 404 as if it did not exist.
Read only: the numbers and the date are the operator's.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.services import quota_service

router = APIRouter(prefix="/api/quotas", tags=["quotas"])


class CounterStanding(BaseModel):
    counter: str
    current: int
    maximum: Optional[int]
    excess: int


class QuotaStanding(BaseModel):
    #: False when the account has no numbers, or fits every one.
    above: bool
    #: When an excess starts to have an effect; null, never.
    effective_at: Optional[datetime]
    in_effect: bool
    #: Bank sync stops while the account holds more connections than its number.
    sync_paused: bool
    counters: list[CounterStanding]


@router.get("/mine", response_model=QuotaStanding)
async def my_standing(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    if not quota_service.enforced():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    standing = await quota_service.standing(session, user.id)
    if standing is None:
        return QuotaStanding(above=False, effective_at=None, in_effect=False, sync_paused=False, counters=[])
    return QuotaStanding(
        above=not standing.fit.allowed,
        effective_at=standing.effective_at,
        in_effect=standing.in_effect,
        sync_paused=standing.sync_paused,
        counters=[
            CounterStanding(counter=f.counter, current=f.current, maximum=f.target, excess=f.excess)
            for f in standing.fit.counters
        ],
    )
