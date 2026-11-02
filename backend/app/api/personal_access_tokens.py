"""Manage personal access tokens for the signed-in user.

Every route here depends on `current_active_user`, which only accepts the
session JWT. A personal access token can therefore never list, mint, or
revoke tokens.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.core.personal_access_tokens import DISPLAY_PREFIX_LENGTH, generate_token, hash_token
from app.core.workspace_context import resolve_workspace
from app.models.personal_access_token import PersonalAccessToken
from app.models.user import User
from app.schemas.personal_access_token import (
    PersonalAccessTokenCreate,
    PersonalAccessTokenCreated,
    PersonalAccessTokenRead,
)

router = APIRouter(prefix="/api/personal-access-tokens", tags=["personal-access-tokens"])


def _read(token: PersonalAccessToken) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "token_prefix": token.token_prefix,
        "workspace_id": token.workspace_id,
        "workspace_name": token.workspace.name,
        "read_only": token.read_only,
        "expires_at": token.expires_at,
        "last_used_at": token.last_used_at,
        "created_at": token.created_at,
    }


@router.get("", response_model=list[PersonalAccessTokenRead])
async def list_personal_access_tokens(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """List the caller's tokens across every workspace."""
    result = await session.execute(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == user.id)
        .options(selectinload(PersonalAccessToken.workspace))
        .order_by(PersonalAccessToken.created_at.desc())
    )
    return [_read(token) for token in result.scalars().all()]


@router.post(
    "", response_model=PersonalAccessTokenCreated, status_code=status.HTTP_201_CREATED
)
async def create_personal_access_token(
    body: PersonalAccessTokenCreate,
    x_workspace_id: Optional[str] = Header(default=None, alias="X-Workspace-Id"),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Mint a token bound to the caller and the active workspace.

    The plaintext is in this response only; the database keeps its hash.
    """
    ctx = await resolve_workspace(x_workspace_id, user, session)
    raw = generate_token()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    token = PersonalAccessToken(
        user_id=user.id,
        workspace_id=ctx.workspace.id,
        name=body.name.strip() or "Token",
        token_hash=hash_token(raw),
        token_prefix=raw[:DISPLAY_PREFIX_LENGTH],
        read_only=body.read_only,
        expires_at=expires_at,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token, attribute_names=["workspace"])
    return {**_read(token), "token": raw}


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_personal_access_token(
    token_id: uuid.UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    token = (
        await session.execute(
            select(PersonalAccessToken).where(
                PersonalAccessToken.id == token_id, PersonalAccessToken.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    await session.delete(token)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
