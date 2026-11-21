from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.backup import BackupContent
from app.schemas.export import BackupRequest
from app.services import backup_service
from app.services.accountant_export_service import build_accountant_export, zip_filename

router = APIRouter(prefix="/api/export", tags=["export"])

# Same cap as the custom report range: ten calendar years covers every
# realistic accounting period without letting one request read the whole
# history of a workspace.
_MAX_ACCOUNTANT_RANGE_YEARS = 10


def _add_years(value: date, years: int) -> date:
    """Add whole calendar years, clamping Feb 29 to Feb 28 when needed."""
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return date(value.year + years, 2, 28)


def _resolve_accountant_range(start_date: date, end_date: date) -> tuple[date, date]:
    """Validate the accountant export range.

    The range must be non-empty, it must end no later than today on the
    workspace calendar and it must stay within
    :data:`_MAX_ACCOUNTANT_RANGE_YEARS` calendar years. A missing date is
    refused by FastAPI itself, because both are required query parameters.
    """
    if end_date < start_date:
        raise HTTPException(
            status_code=422,
            detail="end_date must be on or after start_date",
        )
    if end_date > app_today():
        raise HTTPException(
            status_code=422,
            detail="end_date must be on or before today",
        )
    if end_date > _add_years(start_date, _MAX_ACCOUNTANT_RANGE_YEARS):
        raise HTTPException(
            status_code=422,
            detail=f"Custom range is too wide (max {_MAX_ACCOUNTANT_RANGE_YEARS} years)",
        )
    return start_date, end_date


def _as_download(archive: bytes) -> StreamingResponse:
    today = app_today().isoformat()
    return StreamingResponse(
        iter([archive]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="securo-backup-{today}.zip"'},
    )


@router.get("/accountant")
async def accountant_export(
    start_date: date = Query(..., description="First day of the period"),
    end_date: date = Query(..., description="Last day of the period"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Export the period as two CSV files for an accountant.

    Read-only, so a viewer may export. Only business workspaces have books an
    accountant reconciles, so other kinds are refused. Every query in the
    service is filtered by the current workspace id.
    """
    if ctx.workspace.kind != "business":
        raise HTTPException(
            status_code=403,
            detail="Accountant export is only available for business workspaces",
        )

    start, end = _resolve_accountant_range(start_date, end_date)
    archive = await build_accountant_export(session, ctx, start, end)
    filename = zip_filename(ctx.workspace.name, start, end)
    return StreamingResponse(
        iter([archive]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
