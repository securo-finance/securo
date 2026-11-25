"""A workspace's status: `active`, or `suspended` by an administrator (ADR 0041).

A suspended workspace can be read and exported, and nothing writes into it.
Requests are refused by `WorkspaceContext.require_write`; the scheduled jobs
leave it out of their queries with `in_active_workspace`.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.user import User
from app.models.workspace import WORKSPACE_ACTIVE, Workspace, WorkspaceMember

#: Shown to the person whose write was refused; the frontend translates it by
#: this exact text (`lib/api-error-translations.ts`).
SUSPENDED_DETAIL = "This workspace is suspended, so it can be read and exported but not changed"


def in_active_workspace(workspace_id: Any) -> ColumnElement[bool]:
    """True for rows whose workspace is active.

    Written as "no workspace says otherwise" so that a row with no workspace
    keeps whatever treatment the caller already gives it.
    """
    workspace = aliased(Workspace)
    return ~(
        select(workspace.id)
        .where(workspace.id == workspace_id, workspace.status != WORKSPACE_ACTIVE)
        .exists()
    )


async def is_suspended(session: AsyncSession, workspace_id: Optional[uuid.UUID]) -> bool:
    # Read from the database, not the identity map: a job may have loaded the
    # workspace before the administrator suspended it.
    status = await session.scalar(select(Workspace.status).where(Workspace.id == workspace_id))
    return status is not None and status != WORKSPACE_ACTIVE


async def require_not_suspended(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """For the workspace routes that check ownership instead of the write gate."""
    if await is_suspended(session, workspace_id):
        raise HTTPException(status_code=403, detail=SUSPENDED_DETAIL)


async def list_workspaces(
    session: AsyncSession,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[tuple[Workspace, list[str]]], int]:
    """Every workspace that is not archived, with its owners' emails."""
    conditions = [Workspace.is_archived.is_(False)]
    if search:
        # Two queries, for the same GUID/UUID reason as
        # `workspace_service.list_members`.
        matching_users = await session.scalars(
            select(User.id).where(User.email.icontains(search, autoescape=True))  # type: ignore[attr-defined]
        )
        owned_by_match = select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.role == "owner", WorkspaceMember.user_id.in_(matching_users.all())
        )
        conditions.append(
            Workspace.name.icontains(search, autoescape=True)
            | Workspace.id.in_(owned_by_match)
        )
    total = await session.scalar(select(func.count()).select_from(Workspace).where(*conditions))
    workspaces = list(
        await session.scalars(
            select(Workspace)
            .where(*conditions)
            .order_by(Workspace.name, Workspace.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )
    )
    owners = await _owner_emails(session, [w.id for w in workspaces])
    return [(w, owners.get(w.id, [])) for w in workspaces], total or 0


async def set_status(
    session: AsyncSession, workspace_id: uuid.UUID, status: str
) -> Optional[tuple[Workspace, list[str]]]:
    workspace = await session.get(Workspace, workspace_id, with_for_update=True)
    if workspace is None or workspace.is_archived:
        return None
    if workspace.status != status:
        workspace.status = status
        workspace.status_changed_at = datetime.now(timezone.utc)
    await session.commit()
    owners = await _owner_emails(session, [workspace.id])
    return workspace, owners.get(workspace.id, [])


async def _owner_emails(
    session: AsyncSession, workspace_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    rows = await session.execute(
        select(WorkspaceMember.workspace_id, WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id.in_(workspace_ids), WorkspaceMember.role == "owner"
        )
    )
    members = rows.all()
    users = await session.scalars(select(User).where(User.id.in_({m.user_id for m in members})))  # type: ignore[attr-defined]
    emails = {u.id: u.email for u in users}
    owners: dict[uuid.UUID, list[str]] = {}
    for workspace_id, user_id in members:
        if user_id in emails:
            owners.setdefault(workspace_id, []).append(emails[user_id])
    return {workspace_id: sorted(found) for workspace_id, found in owners.items()}
