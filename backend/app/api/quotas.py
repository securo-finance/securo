"""An account's own usage limits on a managed instance (ADR 0049, #1972).

Read-only and about the caller alone: the operator writes the numbers through
/api/operator, and this answers them back to the account with the count
behind each so the app can show them and explain a `quota_exceeded`.
"""
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


class MyQuotasRead(BaseModel):
    """`enforced` false: the instance holds no account to limits."""

    enforced: bool
    info_url: Optional[str]
    counters: list[CounterUsageRead]


@router.get("/mine", response_model=MyQuotasRead)
async def my_quotas(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    if not quota_service.enforced():
        return MyQuotasRead(enforced=False, info_url=None, counters=[])
    counters = await quota_service.usage(session, user.id)
    return MyQuotasRead(
        enforced=True,
        info_url=get_settings().quota_info_url.strip() or None,
        counters=[CounterUsageRead(**vars(c)) for c in counters],
    )
