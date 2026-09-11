"""Compatibility deps used by the loans v1 router.

Maps the router's expected names onto the project's standard session and
workspace dependencies.
"""
from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace

# Alias used by app.api.v1.loans
get_db = get_async_session


async def require_workspace_access(
    ctx: WorkspaceContext = Depends(current_workspace),
) -> uuid.UUID:
    """Return the resolved workspace id for routes that only need the UUID."""
    return ctx.workspace.id
