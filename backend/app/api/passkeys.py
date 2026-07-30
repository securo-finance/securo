import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.auth import current_active_user, get_jwt_strategy
from app.core.config import get_settings
from app.core.database import get_async_session
from app.core.rate_limit import login_rate_limit
from app.core.redis import get_redis
from app.core.webauthn import resolve_webauthn_context
from app.api.two_factor import _parse_temp_token_payload
from app.models.passkey import UserPasskey
from app.models.user import User
from app.schemas.passkey import (
    PasskeyAuthenticateOptionsRequest,
    PasskeyAuthenticateVerifyRequest,
    PasskeyOptionsResponse,
    PasskeyRead,
    PasskeyRegisterOptionsRequest,
    PasskeyRegisterVerifyRequest,
    PasskeySecondFactorOptionsRequest,
    PasskeySecondFactorVerifyRequest,
)

router = APIRouter()

REGISTER_CHALLENGE_PREFIX = "passkey_register"
AUTHENTICATE_CHALLENGE_PREFIX = "passkey_authenticate"
SECOND_FACTOR_CHALLENGE_PREFIX = "passkey_2fa"


def _options_dict(options: Any) -> dict[str, Any]:
    return json.loads(options_to_json(options))


def _credential_json(credential: dict[str, Any]) -> str:
    return json.dumps(credential)


def _as_base64url(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return bytes_to_base64url(value)
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _challenge_key(prefix: str, challenge_id: str) -> str:
    return f"{prefix}:{challenge_id}"


async def _get_second_factor_user_id(temp_token: str) -> str:
    redis = await get_redis()
    raw_payload = await redis.get(f"2fa_temp:{temp_token}")
    if not raw_payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    payload = _parse_temp_token_payload(raw_payload)
    available_methods = payload.get("available_methods", []) if payload else []
    if payload is None or not isinstance(available_methods, list) or "passkey" not in available_methods:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return str(payload["user_id"])


async def _delete_second_factor_temp_token(temp_token: str) -> None:
    redis = await get_redis()
    await redis.delete(f"2fa_temp:{temp_token}")


async def _store_challenge(prefix: str, payload: dict[str, Any]) -> str:
    settings = get_settings()
    challenge_id = secrets.token_urlsafe(32)
    redis = await get_redis()
    await redis.set(
        _challenge_key(prefix, challenge_id),
        json.dumps(payload),
        ex=settings.webauthn_challenge_ttl_seconds,
    )
    return challenge_id


async def _pop_challenge(prefix: str, challenge_id: str) -> dict[str, Any] | None:
    redis = await get_redis()
    key = _challenge_key(prefix, challenge_id)
    raw = await redis.getdel(key)
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    # Challenges issued before the ceremony was bound to an origin cannot be verified.
    if not payload.get("rp_id") or not payload.get("origin"):
        return None
    return payload


def _credential_transports(credential: dict[str, Any]) -> list[str] | None:
    transports = credential.get("transports")
    if transports is None:
        response = credential.get("response")
        if isinstance(response, dict):
            transports = response.get("transports")
    if not isinstance(transports, list):
        return None
    return [str(item) for item in transports]


async def _verify_passkey_credential(
    *,
    credential: dict[str, Any],
    challenge: dict[str, Any],
    passkey: UserPasskey,
    session: AsyncSession,
) -> User:
    try:
        verification = verify_authentication_response(
            credential=_credential_json(credential),
            expected_challenge=base64url_to_bytes(challenge["challenge"]),
            expected_origin=challenge["origin"],
            expected_rp_id=challenge["rp_id"],
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passkey") from exc

    verified_credential_id = _as_base64url(verification.credential_id)
    if verified_credential_id != passkey.credential_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passkey")

    user = await session.get(User, passkey.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passkey")

    passkey.sign_count = verification.new_sign_count
    passkey.last_used_at = datetime.now(timezone.utc)
    session.add(passkey)
    await session.commit()
    return user


@router.get("/passkeys", response_model=list[PasskeyRead])
async def list_passkeys(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(
        select(UserPasskey)
        .where(UserPasskey.user_id == user.id)
        .order_by(UserPasskey.created_at.asc())
    )
    return result.scalars().all()


@router.post("/passkeys/register/options", response_model=PasskeyOptionsResponse)
async def passkey_registration_options(
    request: Request,
    body: PasskeyRegisterOptionsRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    settings = get_settings()
    context = resolve_webauthn_context(request)
    existing = await session.execute(select(UserPasskey).where(UserPasskey.user_id == user.id))
    exclude_credentials = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id))
        for passkey in existing.scalars().all()
    ]

    options = generate_registration_options(
        rp_id=context.rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=str(user.id).encode("utf-8"),
        user_name=user.email,
        user_display_name=user.email,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=exclude_credentials,
    )

    challenge_id = await _store_challenge(
        REGISTER_CHALLENGE_PREFIX,
        {
            "challenge": bytes_to_base64url(options.challenge),
            "user_id": str(user.id),
            "name": body.name,
            "rp_id": context.rp_id,
            "origin": context.origin,
        },
    )
    return PasskeyOptionsResponse(challenge_id=challenge_id, options=_options_dict(options))


@router.post("/passkeys/register/verify", response_model=PasskeyRead)
async def verify_passkey_registration(
    body: PasskeyRegisterVerifyRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    challenge = await _pop_challenge(REGISTER_CHALLENGE_PREFIX, body.challenge_id)
    if not challenge or challenge.get("user_id") != str(user.id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired challenge")

    try:
        verification = verify_registration_response(
            credential=_credential_json(body.credential),
            expected_challenge=base64url_to_bytes(challenge["challenge"]),
            expected_origin=challenge["origin"],
            expected_rp_id=challenge["rp_id"],
            require_user_verification=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid passkey registration") from exc

    credential_id = _as_base64url(verification.credential_id)
    passkey = UserPasskey(
        user_id=user.id,
        credential_id=credential_id,
        public_key=_as_base64url(verification.credential_public_key),
        sign_count=verification.sign_count,
        name=body.name or challenge.get("name") or "Passkey",
        transports=_credential_transports(body.credential),
        aaguid=_optional_string(getattr(verification, "aaguid", None)),
        device_type=_optional_string(getattr(verification, "credential_device_type", None)),
        backed_up=_optional_bool(getattr(verification, "credential_backed_up", None)),
    )
    session.add(passkey)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passkey is already registered") from exc
    await session.refresh(passkey)
    return passkey


@router.delete("/passkeys/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_passkey(
    passkey_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(
        select(UserPasskey).where(UserPasskey.id == passkey_id, UserPasskey.user_id == user.id)
    )
    passkey = result.scalar_one_or_none()
    if passkey is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")
    await session.delete(passkey)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/passkeys/authenticate/options",
    response_model=PasskeyOptionsResponse,
    dependencies=[Depends(login_rate_limit)],
)
async def passkey_authentication_options(
    request: Request,
    body: PasskeyAuthenticateOptionsRequest,
    session: AsyncSession = Depends(get_async_session),
):
    context = resolve_webauthn_context(request)
    email = body.email.strip().lower() if body.email else None
    expected_user_id: str | None = None

    if email:
        user_result = await session.execute(select(User).where(func.lower(User.email) == email))
        login_user = user_result.scalar_one_or_none()
        if login_user is not None:
            expected_user_id = str(login_user.id)

    options = generate_authentication_options(
        rp_id=context.rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_id = await _store_challenge(
        AUTHENTICATE_CHALLENGE_PREFIX,
        {
            "challenge": bytes_to_base64url(options.challenge),
            "email_bound": email is not None,
            "expected_user_id": expected_user_id,
            "rp_id": context.rp_id,
            "origin": context.origin,
        },
    )
    return PasskeyOptionsResponse(challenge_id=challenge_id, options=_options_dict(options))


@router.post("/passkeys/authenticate/verify", dependencies=[Depends(login_rate_limit)])
async def verify_passkey_authentication(
    body: PasskeyAuthenticateVerifyRequest,
    session: AsyncSession = Depends(get_async_session),
):
    challenge = await _pop_challenge(AUTHENTICATE_CHALLENGE_PREFIX, body.challenge_id)
    if not challenge:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired challenge")

    credential_id = body.credential.get("rawId") or body.credential.get("id")
    if not credential_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing credential ID")

    result = await session.execute(select(UserPasskey).where(UserPasskey.credential_id == credential_id))
    passkey = result.scalar_one_or_none()
    if passkey is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passkey")

    if challenge.get("email_bound"):
        expected_user_id = challenge.get("expected_user_id")
        if expected_user_id is None or str(passkey.user_id) != expected_user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passkey")

    user = await _verify_passkey_credential(
        credential=body.credential,
        challenge=challenge,
        passkey=passkey,
        session=session,
    )

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/passkeys/2fa/options", response_model=PasskeyOptionsResponse, dependencies=[Depends(login_rate_limit)])
async def passkey_second_factor_options(
    request: Request,
    body: PasskeySecondFactorOptionsRequest,
    session: AsyncSession = Depends(get_async_session),
):
    context = resolve_webauthn_context(request)
    user_id = await _get_second_factor_user_id(body.temp_token)
    result = await session.execute(
        select(UserPasskey).where(UserPasskey.user_id == uuid.UUID(user_id)).order_by(UserPasskey.created_at.asc())
    )
    passkeys = result.scalars().all()
    if not passkeys:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    options = generate_authentication_options(
        rp_id=context.rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id))
            for passkey in passkeys
        ],
    )
    challenge_id = await _store_challenge(
        SECOND_FACTOR_CHALLENGE_PREFIX,
        {
            "challenge": bytes_to_base64url(options.challenge),
            "user_id": user_id,
            "rp_id": context.rp_id,
            "origin": context.origin,
        },
    )
    return PasskeyOptionsResponse(challenge_id=challenge_id, options=_options_dict(options))


@router.post("/passkeys/2fa/verify", dependencies=[Depends(login_rate_limit)])
async def verify_passkey_second_factor(
    body: PasskeySecondFactorVerifyRequest,
    session: AsyncSession = Depends(get_async_session),
):
    user_id = await _get_second_factor_user_id(body.temp_token)
    challenge = await _pop_challenge(SECOND_FACTOR_CHALLENGE_PREFIX, body.challenge_id)
    if not challenge or challenge.get("user_id") != user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired challenge")

    credential_id = body.credential.get("rawId") or body.credential.get("id")
    if not credential_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing credential ID")

    result = await session.execute(
        select(UserPasskey).where(
            UserPasskey.credential_id == credential_id,
            UserPasskey.user_id == uuid.UUID(user_id),
        )
    )
    passkey = result.scalar_one_or_none()
    if passkey is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passkey")

    user = await _verify_passkey_credential(
        credential=body.credential,
        challenge=challenge,
        passkey=passkey,
        session=session,
    )
    await _delete_second_factor_temp_token(body.temp_token)

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    return {"access_token": token, "token_type": "bearer"}
