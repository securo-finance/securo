import secrets
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_jwt_strategy, get_user_manager
from app.core.database import get_async_session
from app.core.redis import get_redis
from app.models.passkey import UserPasskey

router = APIRouter()

TEMP_TOKEN_TTL = 300  # 5 minutes


@router.post("/login")
async def login(
    credentials: OAuth2PasswordRequestForm = Depends(),
    user_manager=Depends(get_user_manager),
    session: AsyncSession = Depends(get_async_session),
):
    user = await user_manager.authenticate(credentials)
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="LOGIN_BAD_CREDENTIALS")

    passkey_count = await session.scalar(
        select(func.count()).select_from(UserPasskey).where(UserPasskey.user_id == user.id)
    )
    available_methods = []
    if user.is_2fa_enabled and user.totp_secret:
        available_methods.append("totp")
    if passkey_count and passkey_count > 0:
        available_methods.append("passkey")

    if available_methods:
        # Store temp token in Redis, return 2FA challenge
        r = await get_redis()
        temp_token = secrets.token_urlsafe(32)
        await r.set(
            f"2fa_temp:{temp_token}",
            json.dumps({"user_id": str(user.id), "available_methods": available_methods}),
            ex=TEMP_TOKEN_TTL,
        )
        return {"requires_2fa": True, "temp_token": temp_token, "available_methods": available_methods}

    # Normal login — generate JWT
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/logout")
async def logout():
    return {"detail": "Logged out"}
