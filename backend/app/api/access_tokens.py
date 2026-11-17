"""Manage access tokens for the current workspace.

These routes are not in the access-token route map, so they only answer a
signed-in session: a token can't mint, list or revoke tokens.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_token_scopes import ALL_SCOPES
from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.models.access_token import AccessToken
from app.models.user import User
from app.schemas.access_token import (
    AccessTokenCreate,
    AccessTokenCreated,
    AccessTokenRead,
    AccessTokenScopes,
)
from app.services import access_token_service
from app.services.access_token_service import AccessTokenError

router = APIRouter(prefix="/api/access-tokens", tags=["access-tokens"])


def _read(token: AccessToken) -> dict:
    return {
        "id": token.id,
        "user_id": token.user_id,
        "name": token.name,
        "token_prefix": token.token_prefix,
        "scopes": token.scopes,
        "created_at": token.created_at,
        "expires_at": token.expires_at,
        "last_used_at": token.last_used_at,
        "revoked_at": token.revoked_at,
        "is_active": access_token_service.is_usable(token),
    }


@router.get("/scopes", response_model=AccessTokenScopes)
async def list_scopes(_: User = Depends(current_active_user)):
    return {"scopes": list(ALL_SCOPES)}


@router.get("", response_model=list[AccessTokenRead])
async def list_access_tokens(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    tokens = await access_token_service.list_tokens(
        session,
        workspace_id=ctx.id,
        user_id=ctx.user_id,
        include_all_members=ctx.is_owner,
    )
    return [_read(t) for t in tokens]


@router.post("", response_model=AccessTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_access_token(
    body: AccessTokenCreate,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        token, raw = await access_token_service.create_token(
            session,
            workspace_id=ctx.id,
            user_id=ctx.user_id,
            can_write=ctx.can_write,
            name=body.name,
            scopes=body.scopes,
            expires_in_days=body.expires_in_days,
        )
    except AccessTokenError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return {**_read(token), "token": raw}


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_access_token(
    token_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    revoked = await access_token_service.revoke_token(
        session,
        token_id=token_id,
        workspace_id=ctx.id,
        user_id=ctx.user_id,
        is_owner=ctx.is_owner,
    )
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access token not found")
