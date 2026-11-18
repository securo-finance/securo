from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.models.user import User
from app.schemas.export import BackupRequest, RestoreResult
from app.services.backup_service import build_backup_archive, collect_workspace_export
from app.services.restore_service import RestoreError, open_archive, restore_workspace

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
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Export every entity in the current workspace as a JSON zip.

    Backup is scoped to one workspace at a time — users with multiple
    workspaces back each one up separately. AssetValue inherits its
    workspace from its Asset and is filtered transitively.
    """
    payload = await collect_workspace_export(ctx, session)
    return _as_download(build_backup_archive(payload.files, attachments=payload.attachments))


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
    payload = await collect_workspace_export(ctx, session)
    return _as_download(
        build_backup_archive(payload.files, password, attachments=payload.attachments)
    )


@router.post("/restore", response_model=RestoreResult, status_code=status.HTTP_201_CREATED)
async def restore(
    file: UploadFile = File(...),
    password: str | None = Form(None),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Restore an archive into a **new** workspace owned by the caller.

    Deliberately not workspace-gated: the whole point is a workspace that does
    not exist yet. The archive is never merged into an existing workspace, so
    there is no path that overwrites data the user still has — stronger than a
    confirmation dialog, and the reason the route takes no `X-Workspace-Id`.

    The upload is read through Starlette's spooled file, so a large archive is
    not held in memory, and every row is written in one transaction: a file
    that fails halfway leaves nothing behind.
    """
    try:
        contents = open_archive(file.file, password or None)
        result = await restore_workspace(session, user, contents)
    except RestoreError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        await session.rollback()
        raise
    await session.commit()
    return RestoreResult(**result.__dict__)
