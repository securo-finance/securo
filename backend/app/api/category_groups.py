import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.category_group import CategoryGroupCreate, CategoryGroupRead, CategoryGroupUpdate
from app.services import category_group_service

router = APIRouter(prefix="/api/category-groups", tags=["category-groups"])


@router.get("", response_model=list[CategoryGroupRead])
async def list_groups(
    include_hidden: bool = Query(False),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await category_group_service.get_groups(
        session,
        ctx.workspace.id,
        include_hidden=include_hidden,
    )


@router.post("", response_model=CategoryGroupRead, status_code=status.HTTP_201_CREATED)
async def create_group(
    data: CategoryGroupCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await category_group_service.create_group(session, ctx.workspace.id, ctx.user_id, data)


@router.patch("/{group_id}", response_model=CategoryGroupRead)
async def update_group(
    group_id: uuid.UUID,
    data: CategoryGroupUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        group = await category_group_service.update_group(
            session, group_id, ctx.workspace.id, data
        )
    except category_group_service.CategoryGroupVisibilityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    deleted = await category_group_service.delete_group(session, group_id, ctx.workspace.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Group not found or is a system group",
        )
