"""Create, list, revoke and authenticate access tokens."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_token_scopes import ALL_SCOPES, is_write_scope
from app.models.access_token import AccessToken

TOKEN_PREFIX = "sct_"
# How stale `last_used_at` may get before a request refreshes it. Writing on
# every request would turn each read into a write.
_LAST_USED_RESOLUTION = timedelta(minutes=1)


class AccessTokenError(ValueError):
    """A token request the caller is not allowed to make."""


def looks_like_access_token(raw: str) -> bool:
    return raw.startswith(TOKEN_PREFIX)


def hash_token(raw: str) -> str:
    # The secret is 256 random bits, so a plain digest is enough: there is
    # nothing to brute force, unlike a password.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    # SQLite (tests) hands timestamps back without a timezone.
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def is_usable(token: AccessToken, now: Optional[datetime] = None) -> bool:
    now = now or _now()
    if token.revoked_at is not None:
        return False
    expires_at = _aware(token.expires_at)
    return expires_at is None or expires_at > now


def normalize_scopes(scopes: list[str], *, can_write: bool) -> list[str]:
    """Validate requested scopes against the catalog and the member's role."""
    cleaned = sorted({s.strip() for s in scopes if s and s.strip()})
    if not cleaned:
        raise AccessTokenError("Choose at least one scope.")
    unknown = [s for s in cleaned if s not in ALL_SCOPES]
    if unknown:
        raise AccessTokenError(f"Unknown scope: {', '.join(unknown)}")
    if not can_write and any(is_write_scope(s) for s in cleaned):
        raise AccessTokenError("Your role in this workspace is read-only, so a token can only read.")
    return cleaned


async def create_token(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    can_write: bool,
    name: str,
    scopes: list[str],
    expires_in_days: Optional[int],
) -> tuple[AccessToken, str]:
    """Create a token and return it with its plaintext secret (shown once)."""
    name = name.strip()
    if not name:
        raise AccessTokenError("Give the token a name.")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    token = AccessToken(
        workspace_id=workspace_id,
        user_id=user_id,
        name=name,
        token_prefix=raw[: len(TOKEN_PREFIX) + 6],
        token_hash=hash_token(raw),
        scopes=normalize_scopes(scopes, can_write=can_write),
        created_at=now,
        expires_at=now + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return token, raw


async def list_tokens(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    include_all_members: bool,
) -> list[AccessToken]:
    """Tokens in the workspace: everyone's for an owner, one's own otherwise."""
    stmt = select(AccessToken).where(AccessToken.workspace_id == workspace_id)
    if not include_all_members:
        stmt = stmt.where(AccessToken.user_id == user_id)
    result = await session.execute(stmt.order_by(AccessToken.created_at.desc()))
    return list(result.scalars().all())


async def revoke_token(
    session: AsyncSession,
    *,
    token_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    is_owner: bool,
) -> bool:
    """Revoke a token. Owners can revoke anyone's; others only their own."""
    token = await session.get(AccessToken, token_id)
    if token is None or token.workspace_id != workspace_id:
        return False
    if token.user_id != user_id and not is_owner:
        return False
    if token.revoked_at is None:
        token.revoked_at = _now()
        await session.commit()
    return True


async def authenticate(session: AsyncSession, raw: str) -> Optional[AccessToken]:
    """The usable token matching `raw`, or None."""
    if not looks_like_access_token(raw):
        return None
    result = await session.execute(select(AccessToken).where(AccessToken.token_hash == hash_token(raw)))
    token = result.scalar_one_or_none()
    now = _now()
    if token is None or not is_usable(token, now):
        return None
    last_used = _aware(token.last_used_at)
    if last_used is None or now - last_used > _LAST_USED_RESOLUTION:
        token.last_used_at = now
        await session.commit()
    return token
