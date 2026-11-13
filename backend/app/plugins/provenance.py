"""Fingerprints of what a plugin wrote, to tell untouched items from edited ones.

Each covers what the user can change in the item's editor. When a row no
longer matches the fingerprint recorded at install, the user edited it and
it is theirs. Rules' fingerprint must stay as it is: migration 107 stored
fingerprints computed with it.
"""
import hashlib
import json
import uuid
from typing import Any

from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.rule import Rule


def _fingerprint(content: dict[str, Any]) -> str:
    payload = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rule_content_hash(
    *,
    name: str,
    conditions_op: str,
    conditions: Any,
    actions: Any,
    priority: int,
) -> str:
    """Everything the rule editor can change but `is_active`: switching a
    plugin's rule off doesn't make it the user's own."""
    return _fingerprint(
        {
            "name": name,
            "conditions_op": conditions_op,
            "conditions": conditions,
            "actions": actions,
            "priority": priority,
        }
    )


def category_content_hash(
    *, name: str, icon: str, color: str, group_id: uuid.UUID | str | None, treat_as_transfer: bool
) -> str:
    return _fingerprint(
        {
            "name": name,
            "icon": icon,
            "color": color,
            "group_id": str(group_id) if group_id else None,
            "treat_as_transfer": treat_as_transfer,
        }
    )


def group_content_hash(*, name: str, icon: str, color: str) -> str:
    return _fingerprint({"name": name, "icon": icon, "color": color})


def row_hash(row: Rule | Category | CategoryGroup) -> str:
    """The fingerprint of a row as it is now."""
    if isinstance(row, Rule):
        return rule_content_hash(
            name=row.name,
            conditions_op=row.conditions_op,
            conditions=row.conditions,
            actions=row.actions,
            priority=row.priority or 0,
        )
    if isinstance(row, Category):
        return category_content_hash(
            name=row.name,
            icon=row.icon,
            color=row.color,
            group_id=row.group_id,
            treat_as_transfer=bool(row.treat_as_transfer),
        )
    return group_content_hash(name=row.name, icon=row.icon, color=row.color)


def is_untouched(row: Rule | Category | CategoryGroup) -> bool:
    return row.source_hash is not None and row.source_hash == row_hash(row)
