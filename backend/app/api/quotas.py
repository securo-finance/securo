"""An account's own usage limits on a managed instance (ADR 0049, ADR 0055, #1972).

Read-only and about the caller alone: the operator writes the numbers and the
date through /api/operator, and this answers them back to the account with the
count behind each, how far above it is, and from when that matters, so the app
can show them and explain a `quota_exceeded`.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.config import get_settings
from app.core.database import get_async_session
from app.models.user import User
from app.services import quota_service

router = APIRouter(prefix="/api/quotas", tags=["quotas"])


class CounterUsageRead(BaseModel):
    counter: str
    current: int
    maximum: int
    #: How many to remove to fit; for members, the seats over it summed across workspaces.
    excess: int


class MyQuotasRead(BaseModel):
    """`enforced` false: the instance holds no account to limits."""

    enforced: bool
    info_url: Optional[str]
    #: False when the account has no numbers, or fits every one.
    above: bool
    #: When an excess starts to have an effect; null, never.
    effective_at: Optional[datetime]
    in_effect: bool
    #: Bank sync stops while the account holds more connections than its number.
    sync_paused: bool
    counters: list[CounterUsageRead]


@router.get("/mine", response_model=MyQuotasRead)
async def my_quotas(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    if not quota_service.enforced():
        return MyQuotasRead(
            enforced=False, info_url=None, above=False, effective_at=None,
            in_effect=False, sync_paused=False, counters=[],
        )
    limits, _ = await quota_service.effective_limits(session, user.id)
    fit = await quota_service.check_fit(session, user.id, limits)
    standing = await quota_service.standing(session, user.id)
    return MyQuotasRead(
        enforced=True,
        info_url=get_settings().quota_info_url.strip() or None,
        above=not fit.allowed,
        effective_at=standing.effective_at if standing else None,
        in_effect=standing.in_effect if standing else False,
        sync_paused=standing.sync_paused if standing else False,
        counters=[
            CounterUsageRead(counter=f.counter, current=f.current, maximum=f.target, excess=f.excess)
            for f in fit.counters
            if f.target is not None
        ],
    )
