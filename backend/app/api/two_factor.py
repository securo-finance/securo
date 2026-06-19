import json
import uuid

import pyotp
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user, get_jwt_strategy
from app.core.database import get_async_session
from app.core.redis import get_redis
from app.models.user import User
from app.schemas.two_factor import (
    TwoFactorDisableRequest,
    TwoFactorEnableRequest,
    TwoFactorSetupResponse,
    TwoFactorVerifyRequest,
)

router = APIRouter()


def _parse_temp_token_payload(raw: str | bytes) -> dict[str, object] | None:
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        # Backward compatibility with existing temp tokens that only stored user_id.
        return {"user_id": raw, "available_methods": ["totp"]}
    if not isinstance(payload, dict) or not isinstance(payload.get("user_id"), str):
        return None
    return payload


@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def setup_2fa(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    secret = pyotp.random_base32()
    user.totp_secret = secret
    session.add(user)
    await session.commit()

    totp = pyotp.TOTP(secret)
    otpauth_uri = totp.provisioning_uri(name=user.email, issuer_name="Securo")

    return TwoFactorSetupResponse(secret=secret, otpauth_uri=otpauth_uri)


@router.post("/2fa/enable")
async def enable_2fa(
    body: TwoFactorEnableRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    if not user.totp_secret:
        raise HTTPException(status_code=400, detail="Call /2fa/setup first")

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(body.code):
        raise HTTPException(status_code=400, detail="Invalid 2FA code")

    user.is_2fa_enabled = True
    session.add(user)
    await session.commit()
    return {"detail": "2FA enabled"}


@router.post("/2fa/disable")
async def disable_2fa(
    body: TwoFactorDisableRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    from fastapi_users.db import SQLAlchemyUserDatabase
    from fastapi.security import OAuth2PasswordRequestForm

    # Verify password by authenticating
    user_db = SQLAlchemyUserDatabase(session, User)
    from app.core.auth import UserManager
    user_manager = UserManager(user_db)

    creds = OAuth2PasswordRequestForm(username=user.email, password=body.password)
    authenticated = await user_manager.authenticate(creds)
    if authenticated is None:
        raise HTTPException(status_code=400, detail="Invalid password")

    # Verify TOTP code
    if not user.totp_secret:
        raise HTTPException(status_code=400, detail="2FA is not set up")

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(body.code):
        raise HTTPException(status_code=400, detail="Invalid 2FA code")

    user.totp_secret = None
    user.is_2fa_enabled = False
    session.add(user)
    await session.commit()
    return {"detail": "2FA disabled"}


@router.post("/2fa/verify")
async def verify_2fa(
    body: TwoFactorVerifyRequest,
    session: AsyncSession = Depends(get_async_session),
):
    r = await get_redis()
    redis_key = f"2fa_temp:{body.temp_token}"
    raw_payload = await r.get(redis_key)

    if not raw_payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    payload = _parse_temp_token_payload(raw_payload)
    available_methods = payload.get("available_methods", []) if payload else []
    if payload is None or not isinstance(available_methods, list) or "totp" not in available_methods:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Load user
    result = await session.execute(select(User).where(User.id == uuid.UUID(str(payload["user_id"]))))
    user = result.scalar_one_or_none()
    if not user or not (user.is_2fa_enabled and user.totp_secret):
        raise HTTPException(status_code=401, detail="Invalid token")

    # Verify TOTP
    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(body.code):
        raise HTTPException(status_code=400, detail="Invalid 2FA code")

    # Delete temp token
    await r.delete(redis_key)

    # Generate JWT
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    return {"access_token": token, "token_type": "bearer"}
