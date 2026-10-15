"""The timezones a signed-in person can pick from, and the one in force.

Workspace settings need the list to offer a calendar of its own, and every
client needs to know which day the server calls today; neither is an
administrator's concern, so this lives outside the admin router.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.app_clock import get_timezone, timezone_names
from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/timezones", tags=["timezones"])


class TimezonesRead(BaseModel):
    #: The application timezone: what a workspace without its own follows.
    default: str
    available: list[str]


@router.get("", response_model=TimezonesRead)
async def list_timezones(
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(current_active_user),
):
    return TimezonesRead(
        default=str(await get_timezone(session)),
        available=sorted(timezone_names()),
    )
