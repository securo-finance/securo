import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.rule import RuleCreate, RuleRead, RuleUpdate
from app.services import rule_service
from app.services.rule_service import DuplicateRuleError

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("", response_model=list[RuleRead])
async def list_rules(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    return await rule_service.get_rules(session, user.id)


@router.post("", response_model=RuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(
    data: RuleCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        return await rule_service.create_rule(session, user.id, data)
    except DuplicateRuleError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A rule with this name already exists",
        )


@router.patch("/{rule_id}", response_model=RuleRead)
async def update_rule(
    rule_id: uuid.UUID,
    data: RuleUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    try:
        rule = await rule_service.update_rule(session, rule_id, user.id, data)
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
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    deleted = await rule_service.delete_rule(session, rule_id, user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")


@router.get("/packs", response_model=list[dict])
async def list_rule_packs(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """List available country-specific rule packs with installed status."""
    installed_map = await rule_service.get_installed_packs(session, user.id)
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
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Install a country-specific rule pack."""
    if pack_code not in rule_service.RULE_PACKS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule pack not found")
    lang = (user.preferences or {}).get("language", "pt-BR")
    rules = await rule_service.install_rule_pack(session, user.id, pack_code, lang)
    return {"installed": len(rules)}


@router.post("/apply-all", response_model=dict)
async def apply_all_rules(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    """Re-apply all active rules to all existing transactions."""
    count = await rule_service.apply_all_rules(session, user.id)
    return {"applied": count}
