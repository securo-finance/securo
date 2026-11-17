from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.backup import BackupContent
from app.schemas.export import BackupRequest
from app.services import backup_service

router = APIRouter(prefix="/api/export", tags=["export"])


def _as_download(archive: bytes) -> StreamingResponse:
    today = app_today().isoformat()
    return StreamingResponse(
        iter([archive]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="securo-backup-{today}.zip"'},
    )


@router.get("/backup")
async def backup(
    content: BackupContent = Query(BackupContent.both),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Export the selected workspace content as a restorable JSON ZIP."""
    archive = await backup_service.build_backup_zip(
        session,
        ctx.workspace,
        content=content,
    )
    return _as_download(archive)


@router.post("/backup")
async def backup_protected(
    body: BackupRequest,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """The same archive, encrypted with AES-256 when a password is given.

    A POST because the password belongs in a body: a query string is written
    to browser history, proxy logs and server access logs. Securo never stores
    the password and cannot recover the archive without it.
    """
    password = body.password.get_secret_value() if body.password else None
    archive = await backup_service.build_backup_zip(
        session,
        ctx.workspace,
        content=BackupContent.both,
        password=password,
    )
    return _as_download(archive)
