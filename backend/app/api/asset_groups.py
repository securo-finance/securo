import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.asset_group import AssetGroupCreate, AssetGroupRead, AssetGroupUpdate
from app.services import asset_group_service

router = APIRouter(prefix="/api/asset-groups", tags=["asset-groups"])


@router.get("", response_model=list[AssetGroupRead])
async def list_groups(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await asset_group_service.get_groups(session, ctx.workspace.id, ctx.user_id)


@router.post("", response_model=AssetGroupRead, status_code=status.HTTP_201_CREATED)
async def create_group(
    data: AssetGroupCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await asset_group_service.create_group(session, ctx.workspace.id, ctx.user_id, data)


@router.patch("/{group_id}", response_model=AssetGroupRead)
async def update_group(
    group_id: uuid.UUID,
    data: AssetGroupUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    group = await asset_group_service.update_group(
        session, group_id, ctx.workspace.id, ctx.user_id, data
    )
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    deleted = await asset_group_service.delete_group(session, group_id, ctx.workspace.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )
