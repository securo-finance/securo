"""Password recovery routes (ADR 0041).

These replace fastapi-users' own reset router so the forgot-password answer
can admit it when email is off: with EMAIL_ENABLED unset the route answers
503 instead of pretending a message went out. With it on, a known and an
unknown address get the same 202 and the same body, so the endpoint never
tells anyone which addresses have an account.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi_users import exceptions
from fastapi_users.router.common import ErrorCode
from pydantic import EmailStr

from app.core.auth import UserManager, get_user_manager
from app.core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()

# The same bounds the account forms enforce (AdminUserCreate and UserCreate).
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 72


def _invalid_password(reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": ErrorCode.RESET_PASSWORD_INVALID_PASSWORD,
            "reason": reason,
        },
    )


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Password recovery by email is not enabled on this instance"
        }
    },
)
async def forgot_password(
    request: Request,
    email: EmailStr = Body(..., embed=True),
    user_manager: UserManager = Depends(get_user_manager),
):
    if not get_settings().email_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Password recovery by email is not enabled on this instance",
        )
    try:
        user = await user_manager.get_by_email(email)
    except exceptions.UserNotExists:
        return None

    try:
        await user_manager.forgot_password(user, request)
    except exceptions.UserInactive:
        pass

    return None


@router.post("/reset-password")
async def reset_password(
    request: Request,
    token: str = Body(...),
    password: str = Body(...),
    user_manager: UserManager = Depends(get_user_manager),
):
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise _invalid_password(
            f"Password must be between {MIN_PASSWORD_LENGTH} and "
            f"{MAX_PASSWORD_LENGTH} characters"
        )
    try:
        await user_manager.reset_password(token, password, request)
    except (
        exceptions.InvalidResetPasswordToken,
        exceptions.UserNotExists,
        exceptions.UserInactive,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorCode.RESET_PASSWORD_BAD_TOKEN,
        ) from None
    except exceptions.InvalidPasswordException as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": ErrorCode.RESET_PASSWORD_INVALID_PASSWORD,
                "reason": exc.reason,
            },
        ) from None
