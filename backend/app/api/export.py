import io
import json
import zipfile
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_value import AssetValue
from app.models.budget import Budget
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.goal import Goal
from app.models.import_log import ImportLog
from app.models.payee import Payee, PayeeMapping
from app.models.recurring_transaction import RecurringTransaction
from app.models.bank_connection import BankConnection
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.models.user import User

router = APIRouter(prefix="/api/export", tags=["export"])


from app.core.utils import serialize_model, deserialize_row


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
    from app.services.export_service import parse_import_zip, restore_user_data

    try:
        content = await file.read()
        data_map = parse_import_zip(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid backup file: {str(e)}")

    try:
        await restore_user_data(session, user.id, data_map)
        await session.commit()
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to restore backup: {str(e)}")

    return {"status": "success"}

