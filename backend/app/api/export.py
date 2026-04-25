from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/backup")
async def backup(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    from app.services.export_service import export_user_data

    buf = await export_user_data(session, user.id)
    today = date.today().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="securo-backup-{today}.zip"'},
    )


@router.post("/restore")
async def restore(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    from app.services.export_service import BackupArchiveHandler, restore_user_data

    try:
        content = await file.read()
        data_map = BackupArchiveHandler.parse_import_zip(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid backup file: {str(e)}")

    try:
        await restore_user_data(session, user.id, data_map)
        await session.commit()
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to restore backup: {str(e)}")

    return {"status": "success"}

