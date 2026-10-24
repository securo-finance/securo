from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace, current_writable_workspace
from app.schemas.backup import (
    BackupConfig,
    BackupConfigUpdate,
    BackupContent,
    BackupItem,
    BackupPreview,
    BackupRestoreMode,
    BackupRestoreRequest,
    BackupRestoreResult,
    BackupRunRequest,
)
from app.services import backup_service

router = APIRouter(prefix="/api/backups", tags=["backups"])


@router.get("/config", response_model=BackupConfig)
async def get_config(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await backup_service.get_backup_config(session, ctx.workspace.id)


@router.put("/config", response_model=BackupConfig)
async def update_config(
    body: BackupConfigUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_owner()
    return await backup_service.save_backup_config(
        session,
        ctx.workspace.id,
        body.model_dump(exclude_unset=True),
    )


@router.get("", response_model=list[BackupItem])
async def list_backups(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await backup_service.list_stored_backups(session, ctx.workspace.id)


@router.post("/run", response_model=BackupItem)
async def run_backup(
    body: BackupRunRequest | None = None,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await backup_service.create_stored_backup(
        session,
        ctx.workspace,
        content=body.content if body else None,
    )


@router.post("/preview-upload", response_model=BackupPreview)
async def preview_upload(
    file: UploadFile = File(...),
    ctx: WorkspaceContext = Depends(current_workspace),
):
    _ = ctx
    return backup_service.preview_backup_bytes(await file.read())


@router.post("/restore-upload", response_model=BackupRestoreResult)
async def restore_upload(
    file: UploadFile = File(...),
    content: BackupContent = Form(BackupContent.both),
    mode: BackupRestoreMode = Form(BackupRestoreMode.new_workspace),
    confirmation: str | None = Form(None),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if mode == BackupRestoreMode.current_workspace:
        ctx.require_owner()
    return await backup_service.restore_backup_bytes(
        session,
        data=await file.read(),
        target_user_id=ctx.user_id,
        current_workspace=ctx.workspace,
        content=content,
        mode=mode,
        confirmation=confirmation,
    )


@router.get("/{backup_id}/preview", response_model=BackupPreview)
async def preview_stored_backup(
    backup_id: str,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await backup_service.preview_stored_backup(session, ctx.workspace.id, backup_id)


@router.get("/{backup_id}/download")
async def download_stored_backup(
    backup_id: str,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    filename, data = await backup_service.get_stored_backup_bytes(session, ctx.workspace.id, backup_id)
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{backup_id}/restore", response_model=BackupRestoreResult)
async def restore_stored_backup(
    backup_id: str,
    body: BackupRestoreRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    if body.mode == BackupRestoreMode.current_workspace:
        ctx.require_owner()
    _, data = await backup_service.get_stored_backup_bytes(session, ctx.workspace.id, backup_id)
    return await backup_service.restore_backup_bytes(
        session,
        data=data,
        target_user_id=ctx.user_id,
        current_workspace=ctx.workspace,
        content=body.content,
        mode=body.mode,
        confirmation=body.confirmation,
    )
