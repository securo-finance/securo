import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.rule import RuleCreate, RuleCreateResponse, RuleRead, RuleUpdate
from app.services import rule_service
from app.services.rule_service import DuplicateRuleError

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("", response_model=list[RuleRead])
async def list_rules(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await rule_service.get_rules(session, ctx.workspace.id)


@router.post("", response_model=RuleCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    data: RuleCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        rule = await rule_service.create_rule(session, ctx.workspace.id, ctx.user_id, data)
    except DuplicateRuleError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A rule with this name already exists",
        )
    # Apply the new rule to existing transactions so it takes effect on history
    # immediately, and report how many were touched for a transparent toast.
    applied_count = await rule_service.apply_single_rule(session, ctx.workspace.id, rule)
    response = RuleCreateResponse.model_validate(rule)
    response.applied_count = applied_count
    return response


@router.patch("/{rule_id}", response_model=RuleRead)
async def update_rule(
    rule_id: uuid.UUID,
    data: RuleUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        rule = await rule_service.update_rule(session, rule_id, ctx.workspace.id, data)
    except DuplicateRuleError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A rule with this name already exists",
        )
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    deleted = await rule_service.delete_rule(session, rule_id, ctx.workspace.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")


@router.get("/packs", response_model=list[dict])
async def list_rule_packs(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """List available country-specific rule packs with installed status."""
    installed_map = await rule_service.get_installed_packs(session, ctx.user_id)
    packs = []
    for code, pack in rule_service.RULE_PACKS.items():
        packs.append({
            "code": code,
            "name": pack["name"],
            "flag": pack["flag"],
            "rule_count": len(pack["rules"]),
            "installed": installed_map.get(code, False),
        })
    return packs


@router.post("/packs/{pack_code}/install", response_model=dict)
async def install_rule_pack(
    pack_code: str,
    create_missing_categories: bool = False,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Install a country-specific rule pack.

    `create_missing_categories=true` opts into seeding any default
    categories the pack needs that the user doesn't already have.
    """
    if pack_code not in rule_service.RULE_PACKS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule pack not found")
    lang = (ctx.user.preferences or {}).get("language", "pt-BR")
    result = await rule_service.install_rule_pack(
        session,
        ctx.user_id,
        pack_code,
        lang,
        create_missing_categories=create_missing_categories,
    )
    return {
        "installed": len(result.rules),
        "unresolved": result.unresolved,
        "categories_created": result.categories_created,
    }


@router.post("/apply-all", response_model=dict)
async def apply_all_rules(
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Re-apply all active rules to all existing transactions."""
    count = await rule_service.apply_all_rules(session, ctx.workspace.id)
    return {"applied": count}
