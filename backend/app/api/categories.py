import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.category import (
    CategoryCreate,
    CategoryRead,
    CategoryRuleUsage,
    CategoryUpdate,
    RuleSummary,
)
from app.services import category_service

router = APIRouter(prefix="/api/categories", tags=["categories"])


@router.get("", response_model=list[CategoryRead])
async def list_categories(
    include_hidden: bool = Query(False),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await category_service.get_categories(
        session,
        ctx.workspace.id,
        include_hidden=include_hidden,
    )


@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(
    data: CategoryCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await category_service.create_category(session, ctx.workspace.id, ctx.user_id, data)


@router.get("/{category_id}/rule-usage", response_model=CategoryRuleUsage)
async def category_rule_usage(
    category_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Active rules that assign this category, so hiding it can offer to retire them."""
    rules = await category_service.get_rules_assigning_category(
        session, ctx.workspace.id, category_id
    )
    return CategoryRuleUsage(rules=[RuleSummary(id=r.id, name=r.name) for r in rules])


@router.patch("/{category_id}", response_model=CategoryRead)
async def update_category(
    category_id: uuid.UUID,
    data: CategoryUpdate,
    deactivate_rules: bool = Query(False),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        category = await category_service.update_category(
            session,
            category_id,
            ctx.workspace.id,
            data,
            deactivate_rules=deactivate_rules,
        )
    except category_service.CategoryVisibilityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return category


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        deleted = await category_service.delete_category(
            session, category_id, ctx.workspace.id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Category not found or is a system category",
        )
