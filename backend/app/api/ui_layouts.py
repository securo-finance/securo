"""How someone arranged the dashboard and the reports, per workspace.

Personal: any member can arrange their own view, a viewer included, and
nobody else's. Access tokens can't reach these routes.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.models.ui_layout import UiLayout
from app.schemas.ui_layout import LayoutItem, LayoutsRead, LayoutWrite, Surface

router = APIRouter(prefix="/api/ui/layouts", tags=["ui"])


def _mine(ctx: WorkspaceContext):
    return (UiLayout.user_id == ctx.user_id) & (UiLayout.workspace_id == ctx.id)


@router.get("", response_model=LayoutsRead)
async def get_layouts(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    rows = (await session.execute(select(UiLayout).where(_mine(ctx)))).scalars().all()
    return LayoutsRead(**{row.surface: [LayoutItem(**item) for item in row.items] for row in rows})


@router.put("/{surface}", response_model=list[LayoutItem])
async def save_layout(
    surface: Surface,
    body: LayoutWrite,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    items = [item.model_dump() for item in body.items]
    row = (
        await session.execute(select(UiLayout).where(_mine(ctx), UiLayout.surface == surface).with_for_update())
    ).scalar_one_or_none()
    if row is not None:
        row.items = items
        await session.commit()
        return body.items
    session.add(UiLayout(user_id=ctx.user_id, workspace_id=ctx.id, surface=surface, items=items))
    try:
        await session.commit()
    except IntegrityError:
        # Another tab saved the first arrangement at the same moment: the last write wins.
        await session.rollback()
        await session.execute(update(UiLayout).where(_mine(ctx), UiLayout.surface == surface).values(items=items))
        await session.commit()
    return body.items


@router.delete("/{surface}", status_code=status.HTTP_204_NO_CONTENT)
async def reset_layout(
    surface: Surface,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Back to Securo's default arrangement."""
    await session.execute(delete(UiLayout).where(_mine(ctx), UiLayout.surface == surface))
    await session.commit()
