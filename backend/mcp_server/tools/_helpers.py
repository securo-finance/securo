"""Shared helpers for serializing model rows into LLM-friendly dicts.

Keep payloads small and stable: a transaction returned to the LLM should
have a small set of obviously-named fields, not the full SQLAlchemy row.
"""
from __future__ import annotations

import difflib
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select


def parse_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    return date.fromisoformat(str(v))


def parse_uuid(v: Any) -> Optional[uuid.UUID]:
    if v is None or v == "":
        return None
    if isinstance(v, uuid.UUID):
        return v
    return uuid.UUID(str(v))


def parse_uuid_list(v: Any) -> Optional[list[uuid.UUID]]:
    if v is None:
        return None
    values = v if isinstance(v, (list, tuple)) else [v]
    return [
        u
        for x in values
        if (u := parse_uuid(x)) is not None
    ] or None


def num(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, Decimal):
        return float(x)
    return float(x)


async def resolve_workspace_id(session, ctx) -> uuid.UUID:
    """Return the workspace the call operates in.

    Prefer the explicit `ws_id` claim from the JWT. Fall back to the
    caller's default (first) workspace — supports tokens minted before
    the workspace migration AND keeps single-workspace callers free of
    having to specify a workspace.
    """
    if ctx.workspace_id is not None:
        return ctx.workspace_id
    from app.services.workspace_service import get_default_workspace

    ws = await get_default_workspace(session, ctx.user_id)
    if ws is None:
        raise ValueError("No workspace available for this user")
    return ws.id


def _is_uuid(v: Any) -> bool:
    try:
        uuid.UUID(str(v))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


async def resolve_categories(
    session,
    workspace_id: uuid.UUID,
    *,
    ids: Optional[list[str]] = None,
    names: Optional[list[str]] = None,
) -> tuple[list[uuid.UUID], Optional[dict[str, Any]]]:
    """Turn category ids and/or names into ids, or explain what went wrong.

    Models routinely pass a category *name* where the schema asks for an id
    (or a name in the ``category_ids`` slot). Instead of the opaque
    "category not found" that sent the agent into retry loops, match names
    case-insensitively (exact, then unique prefix/substring) and, when
    nothing matches, return an error carrying ``did_you_mean`` so the next
    call can self-correct without another list_categories round trip.

    Returns ``(ids, None)`` on success or ``([], error_dict)``.
    """
    from app.models.category import Category

    wanted_names: list[str] = [str(n).strip() for n in (names or []) if str(n).strip()]
    resolved: list[uuid.UUID] = []
    for raw in ids or []:
        if _is_uuid(raw):
            resolved.append(uuid.UUID(str(raw)))
        elif str(raw).strip():
            wanted_names.append(str(raw).strip())  # a name smuggled into the id slot
    if not wanted_names:
        return resolved, None

    rows = (
        await session.execute(
            select(Category.id, Category.name).where(Category.workspace_id == workspace_id)
        )
    ).all()
    by_lower = {name.lower(): cid for cid, name in rows}
    all_names = [name for _, name in rows]
    for wanted in wanted_names:
        key = wanted.lower()
        if key in by_lower:
            resolved.append(by_lower[key])
            continue
        partial = [cid for cid, name in rows if key in name.lower() or name.lower().startswith(key)]
        if len(partial) == 1:
            resolved.append(partial[0])
            continue
        suggestions = difflib.get_close_matches(wanted, all_names, n=5, cutoff=0.5)
        if len(partial) > 1:
            suggestions = [name for cid, name in rows if cid in partial][:5] + [
                s for s in suggestions if s not in {name for cid, name in rows if cid in partial}
            ]
        return [], {
            "error": f"category not found: {wanted!r}",
            "did_you_mean": [
                {"id": str(cid), "name": name} for cid, name in rows if name in suggestions
            ][:5],
            "hint": "Pass one of the ids above, or call list_categories to see every category.",
        }
    return resolved, None
