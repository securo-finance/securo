"""Personal access tokens: generation, hashing, and request authentication.

A PAT is a random secret with a recognisable prefix. Requests send it as
`Authorization: Bearer securo_pat_...`, the same header the JWT uses, and
`current_workspace` routes it here instead of to the JWT strategy. Routes
that depend only on `current_active_user` keep using the JWT strategy, which
cannot decode a PAT, so they answer 401 to one.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.personal_access_token import PersonalAccessToken
from app.models.user import User

PAT_PREFIX = "securo_pat_"
# Characters of the plaintext kept for display: the prefix plus a few of the
# random part, enough to tell tokens apart in the settings list.
DISPLAY_PREFIX_LENGTH = len(PAT_PREFIX) + 6
# `last_used_at` is informational. Writing it on every request would turn
# each read into a write, so it is refreshed at most this often.
LAST_USED_RESOLUTION = timedelta(minutes=1)


def generate_token() -> str:
    return PAT_PREFIX + secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    # The secret has 256 bits of entropy, so a fast unsalted hash is enough:
    # there is nothing to brute-force that a slow hash would protect.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_pat(raw: Optional[str]) -> bool:
    return bool(raw) and raw.startswith(PAT_PREFIX)


def bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return credentials.strip() or None


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def authenticate_pat(
    raw: str, session: AsyncSession
) -> tuple[PersonalAccessToken, User]:
    """Resolve a PAT to its row and active user, or raise 401."""
    token = (
        await session.execute(
            select(PersonalAccessToken).where(PersonalAccessToken.token_hash == hash_token(raw))
        )
    ).scalar_one_or_none()
    if token is None:
        raise _unauthorized()

    now = datetime.now(timezone.utc)
    if token.expires_at is not None and _aware(token.expires_at) <= now:
        raise _unauthorized()

    user = await session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise _unauthorized()

    if token.last_used_at is None or now - _aware(token.last_used_at) >= LAST_USED_RESOLUTION:
        await session.execute(
            update(PersonalAccessToken)
            .where(PersonalAccessToken.id == token.id)
            .values(last_used_at=now)
        )
        await session.commit()
        token.last_used_at = now

    return token, user


def _aware(value: datetime) -> datetime:
    # SQLite (tests) hands back naive datetimes for timezone-aware columns.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
