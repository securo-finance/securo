import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.transaction import TransactionImportPreview, TransactionImportRequest
from app.services import import_service
from app.services import account_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transactions", tags=["import"])


@router.post("/import/preview", response_model=TransactionImportPreview)
async def preview_import(
    file: UploadFile = File(...),
    date_format: Optional[str] = Form(None),
    flip_amount: bool = Form(False),
    inflow_column: Optional[str] = Form(None),
    outflow_column: Optional[str] = Form(None),
    user: User = Depends(current_active_user),
):
    content = await file.read()
    filename = file.filename or ""

    logger.info(
        "Import preview requested: filename=%s, size=%d bytes, content_type=%s",
        filename, len(content), file.content_type,
    )

    try:
        if filename.lower().endswith('.ofx') or filename.lower().endswith('.qfx'):
            transactions = import_service.parse_ofx(content)
            detected_format = "ofx"
        elif filename.lower().endswith('.qif'):
            transactions = import_service.parse_qif(content)
            detected_format = "qif"
        elif filename.lower().endswith('.xml') or filename.lower().endswith('.camt'):
            transactions = import_service.parse_camt(content)
            detected_format = "camt"
        elif filename.lower().endswith('.csv'):
            transactions = import_service.parse_csv(
                content,
                date_format=date_format,
                flip_amount=flip_amount,
                inflow_column=inflow_column,
                outflow_column=outflow_column,
            )
            detected_format = "csv"
        else:
            # Try to detect format
            try:
                transactions = import_service.parse_ofx(content)
                detected_format = "ofx"
            except Exception:
                try:
                    transactions = import_service.parse_qif(content)
                    detected_format = "qif"
                except Exception:
                    try:
                        transactions = import_service.parse_camt(content)
                        detected_format = "camt"
                    except Exception:
                        transactions = import_service.parse_csv(content)
                        detected_format = "csv"
    except Exception as e:
        logger.error(
            "Failed to parse import file: filename=%s, size=%d bytes, "
            "content_type=%s, first_100_bytes=%r, error=%s",
            filename, len(content), file.content_type,
            content[:100], e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse file: {str(e)}",
        )

    logger.info(
        "Import preview parsed: filename=%s, format=%s, transactions=%d",
        filename, detected_format, len(transactions),
    )

    return TransactionImportPreview(transactions=transactions, detected_format=detected_format)


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_transactions(
    data: TransactionImportRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    # Verify account belongs to user
    account = await account_service.get_account(session, data.account_id, user.id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    imported, skipped, import_log_id = await import_service.import_transactions(
        session, user.id, data.account_id, data.transactions, "import",
        filename=data.filename, detected_format=data.detected_format,
        detect_duplicates=data.detect_duplicates,
    )

    return {"imported": imported, "skipped": skipped, "import_log_id": str(import_log_id)}
