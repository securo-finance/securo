"""FastAPI dependency that resolves the current workspace for a request.

Resolution order:
  1. `X-Workspace-Id` header — explicit selection from the frontend.
  2. The user's first/default workspace (`get_default_workspace`).

If neither resolves, raises 404. Returns a `WorkspaceContext` value
object that carries the workspace + the requester's membership + the
inferred role. Routes that mutate data check `ctx.role` to enforce
viewer/editor/owner restrictions; routes that just read use any
membership.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services.workspace_service import (
    _virtual_manager_member,
    get_default_workspace,
    get_membership,
    is_workspace_manager,
)


@dataclass
class WorkspaceContext:
    workspace: Workspace
    member: WorkspaceMember
    user: User

    @property
    def id(self) -> uuid.UUID:
        return self.workspace.id

    @property
    def user_id(self) -> uuid.UUID:
        """The current human's user_id — used as `created_by` on new rows."""
        return self.user.id

    @property
    def role(self) -> str:
        return self.member.role

    @property
    def can_write(self) -> bool:
        return self.role in ("owner", "editor", "manager")

    @property
    def is_owner(self) -> bool:
        # Workspace managers get effective owner rights for permission gates.
        return self.role in ("owner", "manager")

    def require_write(self) -> None:
        if not self.can_write:
            raise HTTPException(status_code=403, detail="Read-only role")

    def require_owner(self) -> None:
        if not self.is_owner:
            raise HTTPException(status_code=403, detail="Owner role required")


async def current_workspace(
    x_workspace_id: Optional[str] = Header(default=None, alias="X-Workspace-Id"),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> WorkspaceContext:
    """Dependency: resolve and validate the current workspace + membership."""
    if x_workspace_id:
        try:
            ws_uuid = uuid.UUID(x_workspace_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid X-Workspace-Id")
        workspace = await session.get(Workspace, ws_uuid)
        if workspace is None or workspace.is_archived:
            raise HTTPException(status_code=404, detail="Workspace not found")
        member = await get_membership(session, ws_uuid, user.id)
        if member is None:
            # Fall back to manager-of access.
            if await is_workspace_manager(session, ws_uuid, user.id):
                member = _virtual_manager_member(ws_uuid, user.id)
            else:
                raise HTTPException(status_code=404, detail="Workspace not found")
        return WorkspaceContext(workspace=workspace, member=member, user=user)

    # Fallback: user's first non-archived workspace (member-of or managed).
    default = await get_default_workspace(session, user.id)
    if default is None:
        raise HTTPException(status_code=404, detail="No workspace available")
    member = await get_membership(session, default.id, user.id)
    if member is None:
        # Managed-only workspace path.
        if await is_workspace_manager(session, default.id, user.id):
            member = _virtual_manager_member(default.id, user.id)
        else:
            raise HTTPException(status_code=500, detail="Workspace state inconsistent")
    return WorkspaceContext(workspace=default, member=member, user=user)


# Convenience: write-gated context. Raises 403 if the user can't write.
async def current_writable_workspace(
    ctx: WorkspaceContext = Depends(current_workspace),
) -> WorkspaceContext:
    ctx.require_write()
    return ctx
