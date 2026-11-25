"""What the requester should know about the current workspace (ADR 0041).

Personal: any member, a viewer included, reads and dismisses their own and
nobody else's. Access tokens can't reach these routes.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.notification import (
    NotificationKind,
    NotificationList,
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
    NotificationRead,
    NotificationStatus,
)
from app.services import notification_channels, notification_service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationList)
async def list_notifications(
    status_filter: NotificationStatus = Query(default="open", alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    items, total = await notification_service.list_for_user(
        session, ctx.id, ctx.user_id, status=status_filter, limit=limit, offset=offset
    )
    return NotificationList(
        items=[NotificationRead.model_validate(item) for item in items],
        total=total,
        unread_count=await notification_service.unread_count(session, ctx.id, ctx.user_id),
    )


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    await notification_service.mark_all_read(session, ctx.id, ctx.user_id)


@router.get("/preferences", response_model=list[NotificationPreferenceRead])
async def list_preferences(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """The kinds the requester can get in this workspace, and where they want each."""
    chosen = await notification_channels.preferences(session, ctx.id, ctx.user_id)
    available = await notification_channels.available_to(session, ctx.user)
    return [
        NotificationPreferenceRead(
            kind=kind,
            channels={name: notification_channels.wants(chosen, kind, name) for name in available},
        )
        for kind in notification_service.kinds_for(ctx.workspace, can_write=ctx.can_write)
    ]


@router.put("/preferences/{kind}/{channel}", status_code=status.HTTP_204_NO_CONTENT)
async def set_preference(
    kind: NotificationKind,
    channel: str,
    body: NotificationPreferenceUpdate,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if channel not in await notification_channels.available_to(session, ctx.user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    await notification_channels.set_preference(
        session, ctx.id, ctx.user_id, kind, channel, enabled=body.enabled
    )


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    notification_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if not await notification_service.mark_read(session, ctx.id, ctx.user_id, notification_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
