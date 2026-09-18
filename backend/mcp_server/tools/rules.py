"""Expose the existing categorization-rules API contracts through MCP."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rules import _rule_match_definition_changed
from app.schemas.rule import RuleCreate, RulePreviewRequest, RuleRead, RuleUpdate
from app.services import rule_service
from mcp_server.auth import CallContext
from mcp_server.registry import tool
from mcp_server.tools._helpers import resolve_workspace_id
from mcp_server.tools.proposals import _APPLY_FIELD, _PROPOSAL_PREFACE, _can_apply


_RULE_CAPABILITIES = (
    " Conditions combine with AND/OR and filter description, payee, notes, "
    "amount, type, account_id, payee_id, or date. Actions can set category, "
    "payee, or description, append notes, or ignore the transaction."
)


def _proposal_schema(
    model: type[BaseModel], *, include_rule_id: bool = False
) -> dict[str, Any]:
    schema = model.model_json_schema()
    if include_rule_id:
        schema["properties"]["rule_id"] = {"type": "string", "format": "uuid"}
        schema.setdefault("required", []).append("rule_id")
    schema["properties"]["apply"] = _APPLY_FIELD
    return schema


@tool(
    name="list_rules",
    description=(
        "List every categorization rule in the current workspace, including "
        "its ordered conditions, actions, priority, and active state. Use this "
        "before proposing an update or deletion so the user can identify the "
        "exact rule."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    tags=["read", "rules"],
)
async def list_rules(*, session: AsyncSession, ctx: CallContext) -> dict[str, Any]:
    workspace_id = await resolve_workspace_id(session, ctx)
    rules = await rule_service.get_rules(session, workspace_id)
    items = [
        RuleRead.model_validate(rule).model_dump(mode="json", exclude={"user_id"})
        for rule in rules
    ]
    return {"items": items, "total": len(items)}


@tool(
    name="preview_rule",
    description=(
        "Preview an advanced categorization rule without saving or applying it. "
        "Returns exact match/change counts and a page of matching transactions. "
        "Use category, payee, and account UUIDs obtained from their list tools."
    )
    + _RULE_CAPABILITIES,
    parameters=RulePreviewRequest.model_json_schema(),
    tags=["read", "rules"],
)
async def preview_rule(
    *,
    session: AsyncSession,
    ctx: CallContext,
    conditions: list[dict[str, Any]],
    actions: list[dict[str, Any]] | None = None,
    conditions_op: str = "and",
    is_active: bool = True,
    apply_to_existing: bool = True,
    overwrite_existing_categories: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    workspace_id = await resolve_workspace_id(session, ctx)
    draft = RulePreviewRequest.model_validate(
        {
            "conditions_op": conditions_op,
            "conditions": conditions,
            "actions": actions or [],
            "is_active": is_active,
            "apply_to_existing": apply_to_existing,
            "overwrite_existing_categories": overwrite_existing_categories,
            "limit": limit,
            "offset": offset,
        }
    )
    result = await rule_service.preview_rule(
        session,
        workspace_id,
        draft.conditions_op,
        [condition.model_dump() for condition in draft.conditions],
        [action.model_dump() for action in draft.actions],
        is_active=draft.is_active,
        apply_to_existing=draft.apply_to_existing,
        overwrite_existing_categories=draft.overwrite_existing_categories,
        limit=draft.limit,
        offset=draft.offset,
    )
    return result.model_dump(mode="json")


@tool(
    name="propose_create_rule",
    description=_PROPOSAL_PREFACE
    + (
        "Preview creation of an advanced categorization rule with multiple "
        "conditions and actions. The response includes the same transaction "
        "impact preview as preview_rule. Use IDs returned by the corresponding list tools."
    )
    + _RULE_CAPABILITIES,
    parameters=_proposal_schema(RuleCreate),
    is_proposal=True,
    tags=["propose", "rules"],
)
async def propose_create_rule(
    *,
    session: AsyncSession,
    ctx: CallContext,
    name: str,
    conditions: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    conditions_op: str = "and",
    priority: int = 0,
    is_active: bool = True,
    apply_to_existing: bool = True,
    overwrite_existing_categories: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    workspace_id = await resolve_workspace_id(session, ctx)
    draft = RuleCreate.model_validate(
        {
            "name": name,
            "conditions_op": conditions_op,
            "conditions": conditions,
            "actions": actions,
            "priority": priority,
            "is_active": is_active,
            "apply_to_existing": apply_to_existing,
            "overwrite_existing_categories": overwrite_existing_categories,
        }
    )
    preview = await rule_service.preview_rule(
        session,
        workspace_id,
        draft.conditions_op,
        [condition.model_dump() for condition in draft.conditions],
        [action.model_dump() for action in draft.actions],
        is_active=draft.is_active,
        apply_to_existing=draft.apply_to_existing,
        overwrite_existing_categories=draft.overwrite_existing_categories,
    )
    proposal = {
        "kind": "create_rule",
        "proposed": draft.model_dump(mode="json"),
        "preview": preview.model_dump(mode="json"),
        "apply_endpoint": "POST /api/rules",
    }
    if not _can_apply(ctx, apply):
        return proposal

    created = await rule_service.create_rule(session, workspace_id, ctx.user_id, draft)
    applied_count = (
        await rule_service.apply_single_rule(
            session,
            workspace_id,
            created,
            overwrite_existing_categories=draft.overwrite_existing_categories,
        )
        if draft.apply_to_existing
        else 0
    )
    return {
        **proposal,
        "applied": True,
        "id": str(created.id),
        "applied_count": applied_count,
    }


@tool(
    name="propose_update_rule",
    description=_PROPOSAL_PREFACE
    + (
        "Preview changes to an existing categorization rule and their impact. "
        "Call list_rules first and pass only the fields the user wants changed."
    )
    + _RULE_CAPABILITIES,
    parameters=_proposal_schema(RuleUpdate, include_rule_id=True),
    is_proposal=True,
    tags=["propose", "rules"],
)
async def propose_update_rule(
    *,
    session: AsyncSession,
    ctx: CallContext,
    rule_id: str,
    name: str | None = None,
    conditions_op: str | None = None,
    conditions: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    priority: int | None = None,
    is_active: bool | None = None,
    apply_to_existing: bool | None = None,
    overwrite_existing_categories: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    workspace_id = await resolve_workspace_id(session, ctx)
    rule = await rule_service.get_rule(session, uuid.UUID(rule_id), workspace_id)
    if rule is None:
        return {"error": "rule not found"}

    changes: dict[str, Any] = {}
    for key, value in (
        ("name", name),
        ("conditions_op", conditions_op),
        ("conditions", conditions),
        ("actions", actions),
        ("priority", priority),
        ("is_active", is_active),
        ("apply_to_existing", apply_to_existing),
    ):
        if value is not None:
            changes[key] = value
    if overwrite_existing_categories:
        changes["overwrite_existing_categories"] = True
    if not changes:
        return {"error": "no changes provided"}

    update = RuleUpdate(**changes)
    current = RuleRead.model_validate(rule)
    should_apply = (
        update.apply_to_existing
        if update.apply_to_existing is not None
        else _rule_match_definition_changed(current, update)
    )
    preview = await rule_service.preview_rule(
        session,
        workspace_id,
        update.conditions_op or current.conditions_op,
        [condition.model_dump() for condition in update.conditions]
        if update.conditions is not None
        else current.conditions,
        [action.model_dump() for action in update.actions]
        if update.actions is not None
        else current.actions,
        is_active=update.is_active if update.is_active is not None else current.is_active,
        apply_to_existing=should_apply,
        overwrite_existing_categories=update.overwrite_existing_categories,
    )
    proposal = {
        "kind": "update_rule",
        "target": current.model_dump(mode="json", exclude={"user_id"}),
        "changes": update.model_dump(mode="json", exclude_unset=True),
        "preview": preview.model_dump(mode="json"),
        "apply_endpoint": f"PATCH /api/rules/{rule_id}",
    }
    if not _can_apply(ctx, apply):
        return proposal

    updated = await rule_service.update_rule(
        session, uuid.UUID(rule_id), workspace_id, update
    )
    if updated is None:
        return {"error": "rule not found"}
    applied_count = (
        await rule_service.apply_single_rule(
            session,
            workspace_id,
            updated,
            overwrite_existing_categories=update.overwrite_existing_categories,
        )
        if should_apply
        else 0
    )
    return {
        **proposal,
        "applied": True,
        "id": str(updated.id),
        "applied_count": applied_count,
    }


@tool(
    name="propose_delete_rule",
    description=_PROPOSAL_PREFACE
    + "Preview deletion of one existing categorization rule. Call list_rules first.",
    parameters={
        "type": "object",
        "properties": {
            "rule_id": {"type": "string", "format": "uuid"},
            "apply": _APPLY_FIELD,
        },
        "required": ["rule_id"],
        "additionalProperties": False,
    },
    is_proposal=True,
    tags=["propose", "rules"],
)
async def propose_delete_rule(
    *,
    session: AsyncSession,
    ctx: CallContext,
    rule_id: str,
    apply: bool = False,
) -> dict[str, Any]:
    workspace_id = await resolve_workspace_id(session, ctx)
    rule = await rule_service.get_rule(session, uuid.UUID(rule_id), workspace_id)
    if rule is None:
        return {"error": "rule not found"}
    proposal = {
        "kind": "delete_rule",
        "target": RuleRead.model_validate(rule).model_dump(
            mode="json", exclude={"user_id"}
        ),
        "apply_endpoint": f"DELETE /api/rules/{rule_id}",
    }
    if not _can_apply(ctx, apply):
        return proposal

    await rule_service.delete_rule(session, uuid.UUID(rule_id), workspace_id)
    return {**proposal, "applied": True, "id": rule_id}
