"""Which modules an account's workspaces may use on a managed instance (ADR 0054).

Neutral, like the quotas of ADR 0049: the operator writes a list of module ids
per account and the code attaches nothing else to it. No list means every
module, so an account the operator never touched behaves as before.

The list lives in `user_module_limits` and is copied onto every workspace the
account created (`Workspace.module_allowlist`), by `created_by_user_id` as the
quota counts are. The copy is what `module_service` reads: resolving a
workspace's modules stays a pure function of the workspace row, with no query
of its own on every request. Writing the list and copying it happen in the same
transaction, and a workspace created later takes the copy when it is created.

A module left out resolves as off; its rows are never touched, so putting it
back on the list brings everything back.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_module_limit import UserModuleLimit
from app.models.workspace import Workspace
from app.services.module_service import ModuleId


def normalize(modules: Iterable[str]) -> list[str]:
    """Sorted, without repeats. Raises ValueError on an id outside the catalog."""
    return sorted({ModuleId(m).value for m in modules})


async def _lock(session: AsyncSession, user_id: uuid.UUID) -> None:
    """One write of the account's list, or one read of it for a new workspace, at a time.

    Without it, under READ COMMITTED a workspace being created could read no
    list, the operator could then commit one and copy it onto the committed
    workspaces, and the new workspace would land with no list at all. Held
    until the transaction ends. Taken after any quota lock and never before
    one, so the two cannot wait on each other. PostgreSQL only, in the same
    shape as `quota_service._lock`: SQLite serialises writers itself.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"module_limit:{user_id}")))
    )


async def get_limit(session: AsyncSession, user_id: uuid.UUID) -> Optional[list[str]]:
    row = await session.get(UserModuleLimit, user_id)
    return list(row.modules) if row else None


async def set_limit(
    session: AsyncSession, user_id: uuid.UUID, modules: Optional[Iterable[str]]
) -> Optional[list[str]]:
    """Write the account's list (None clears it) and copy it onto its workspaces.

    Flush only; the caller commits.
    """
    allowed = normalize(modules) if modules is not None else None
    await _lock(session, user_id)
    row = await session.get(UserModuleLimit, user_id)
    if allowed is None:
        if row is not None:
            await session.delete(row)
    elif row is None:
        session.add(UserModuleLimit(user_id=user_id, modules=allowed))
    else:
        row.modules = allowed
    await session.execute(
        update(Workspace)
        .where(Workspace.created_by_user_id == user_id)
        .values(module_allowlist=allowed)
        .execution_options(synchronize_session="fetch")
    )
    await session.flush()
    return allowed


async def stamp_new_workspace(session: AsyncSession, workspace: Workspace) -> None:
    """Give a workspace being created its creator's list.

    Read whether or not MANAGED_INSTANCE is on: it is one primary-key lookup
    when a workspace is created, and it keeps the copy right if the instance is
    switched to managed later. The list itself is only enforced when it is on.
    """
    if workspace.created_by_user_id is None:
        return
    await _lock(session, workspace.created_by_user_id)
    workspace.module_allowlist = await get_limit(session, workspace.created_by_user_id)
