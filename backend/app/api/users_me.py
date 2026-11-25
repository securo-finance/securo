"""The owner's own profile update, with the account's row taken first (#567).

fastapi-users builds `PATCH /api/users/me` from the account its authenticator
loaded, and writes it afterwards. An administrator deleting that account at the
same moment commits in between, so the write matched no row: SQLAlchemy raised
``StaleDataError`` and the profile edit answered 500 in two rounds of twelve.
Taking the row with a lock before anything is written makes the two queue, the
way the administrative routes already do: if the deletion went first this
answers 404 instead of failing.

Only this route replaces the library's; `GET /api/users/me` stays the one
fastapi-users mounts (see `app/main.py`).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi_users import exceptions
from fastapi_users.manager import BaseUserManager
from fastapi_users.router.common import ErrorCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import fastapi_users
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.user import UserRead, UserUpdate

GONE = "This account no longer exists"

router = APIRouter(prefix="/api/users/me", tags=["users"])

# The same dependency the library's route used: a session JWT, and nothing an
# access token can reach (an unlisted route, ADR 0012).
current_user = fastapi_users.current_user(active=True)


@router.patch(
    "",
    response_model=UserRead,
    name="users:patch_current_user",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Missing token or inactive user."}},
)
async def update_me(
    request: Request,
    user_update: UserUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_async_session),
    user_manager: BaseUserManager = Depends(fastapi_users.get_user_manager),
):
    """Change the caller's own account, once its row is taken for writing."""
    locked = (
        await session.execute(
            select(User)
            .where(User.id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked is None:
        # The deletion went first, so this account is no longer there.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=GONE)

    try:
        updated = await user_manager.update(
            user_update, locked, safe=True, request=request
        )
    except exceptions.InvalidPasswordException as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": ErrorCode.UPDATE_USER_INVALID_PASSWORD,
                "reason": exc.reason,
            },
        ) from exc
    except exceptions.UserAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorCode.UPDATE_USER_EMAIL_ALREADY_EXISTS,
        ) from exc
    return UserRead.model_validate(updated)
