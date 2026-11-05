"""Endpoint-scoped locked-current preferences merge; no ambient session factory."""
import sqlite3
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.dashboard import CommittedDashboardLayout, DashboardLayoutWrite
from app.services.workspace_service import require_membership

SQLITE_BUSY_TIMEOUT_MS = 1_000


def _sqlite_contention(exc: OperationalError) -> bool:
    # Extended SQLite error codes retain the primary code in the low byte.
    code = getattr(exc.orig, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & 0xFF) in (
        sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
    )


def _merged_preferences(current, sql_null: bool, workspace_id: str, submitted: dict) -> dict:
    if sql_null:
        current = {}
    if not isinstance(current, dict):
        raise HTTPException(status_code=409, detail="PREFERENCES_SHAPE_CONFLICT")
    layouts = current.get("dashboard_layouts", {})
    if not isinstance(layouts, dict):
        raise HTTPException(status_code=409, detail="PREFERENCES_SHAPE_CONFLICT")
    target = {}
    if workspace_id in layouts:
        target = layouts[workspace_id]
        if not isinstance(target, dict):
            raise HTTPException(status_code=409, detail="LAYOUT_TARGET_CONFLICT")
        # Stored target extras are not write-body extras. Validate only its
        # reserved projection, then retain the other top-level target fields.
        projection = {key: target[key] for key in ("version", "workspaceId", "items")
                      if key in target}
        try:
            stored = DashboardLayoutWrite.model_validate(projection)
        except ValidationError as exc:
            raise HTTPException(status_code=409, detail="LAYOUT_TARGET_CONFLICT") from exc
        if stored.workspaceId != workspace_id:
            raise HTTPException(status_code=409, detail="LAYOUT_TARGET_CONFLICT")
    return {
        **current,
        "dashboard_layouts": {**layouts, workspace_id: {**target, **submitted}},
    }


async def save_dashboard_layout(
    *,
    actor_id: uuid.UUID,
    workspace_id: uuid.UUID,
    layout: DashboardLayoutWrite,
    session_factory: async_sessionmaker[AsyncSession],
) -> CommittedDashboardLayout:
    """One fresh transaction, one preferences assignment/flush/commit, no retry.

    An exception during/after commit does not imply the write was absent.
    Propagate it without a success response or a blind replay; the caller must
    recheck current state before retrying an unknown-outcome operation.
    """
    if layout.workspaceId != str(workspace_id):
        raise HTTPException(status_code=422, detail="workspaceId must match the path")
    submitted = layout.model_dump()
    async with session_factory() as session:
        dialect = session.get_bind().dialect.name
        try:
            if dialect == "sqlite":
                # SQLAlchemy's logical autobegin here has not issued a driver
                # read. Acquire SQLite's write reservation before ALL reads.
                await session.execute(text(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}"))
                await session.execute(text("BEGIN IMMEDIATE"))
            elif dialect == "postgresql":
                await session.begin()
            else:
                raise HTTPException(status_code=503, detail="PREFERENCES_STORAGE_UNAVAILABLE")

            workspace = await session.get(Workspace, workspace_id)
            if workspace is None or workspace.is_archived:
                raise HTTPException(status_code=404, detail="Workspace not found")
            try:
                await require_membership(session, workspace_id, actor_id, min_role="viewer")
            except HTTPException as exc:
                if exc.status_code in (403, 404):
                    raise HTTPException(status_code=404, detail="Workspace not found") from exc
                raise

            query = select(User, User.preferences.is_(None).label("preferences_sql_null")).where(
                User.id == actor_id
            )
            if dialect == "postgresql":
                query = query.with_for_update(of=User)
            row = (await session.execute(query)).one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="User not found")
            user, sql_null = row
            user.preferences = _merged_preferences(
                user.preferences, sql_null, str(workspace_id), submitted
            )
            await session.flush()
            await session.commit()
        except BaseException as exc:
            await session.rollback()
            if dialect == "sqlite" and isinstance(exc, OperationalError) and _sqlite_contention(exc):
                raise HTTPException(status_code=503, detail="PREFERENCES_STORAGE_UNAVAILABLE") from exc
            raise

    # No full-user snapshot or post-commit ORM load (also safe with expiration).
    return CommittedDashboardLayout(
        workspaceId=str(workspace_id), layout=DashboardLayoutWrite.model_validate(submitted)
    )
