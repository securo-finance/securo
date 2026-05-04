import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_active_user
from app.core.database import get_async_session
from app.models.user import User
from app.schemas.transaction import (
    C6CardPreview,
    TransactionImportPreview,
    TransactionImportRequest,
)
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
    password: Optional[str] = Form(None),
    user: User = Depends(current_active_user),
):
    content = await file.read()
    filename = file.filename or ""

    logger.info(
        "Import preview requested: filename=%s, size=%d bytes, content_type=%s",
        filename, len(content), file.content_type,
    )

    is_pdf = filename.lower().endswith('.pdf') or (file.content_type or '').startswith('application/pdf')

    try:
        if is_pdf:
            import pikepdf
            import io as _io

            # Step 1: try unencrypted text extraction (PicPay case)
            institution = None
            plain_text = None
            try:
                plain_text = import_service._extract_pdf_text(content)
                institution = import_service.detect_pdf_institution(plain_text)
            except Exception:
                pass

            if institution == 'picpay':
                transactions = import_service._parse_picpay_text(plain_text)
                return TransactionImportPreview(
                    transactions=transactions,
                    detected_format='pdf',
                    institution='picpay',
                )

            # Step 2: unencrypted extraction gave no known institution.
            # Check if PDF is password-protected (C6 case).
            if institution is None:
                is_encrypted = False
                try:
                    pikepdf.open(_io.BytesIO(content))
                except pikepdf.PasswordError:
                    is_encrypted = True
                except Exception:
                    pass

                if is_encrypted:
                    if not password:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail='password_required',
                        )
                    try:
                        text_enc = import_service._extract_pdf_text_encrypted(content, password)
                    except ValueError as exc:
                        if 'invalid_password' in str(exc):
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail='invalid_password',
                            )
                        raise
                    institution = import_service.detect_pdf_institution(text_enc)
                    if institution == 'c6':
                        cards_dict = import_service._parse_c6_text(text_enc)
                        if not cards_dict:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail='no_transactions',
                            )
                        all_txns = [t for txns in cards_dict.values() for t in txns]
                        cards_preview = [
                            C6CardPreview(
                                card_last4=last4,
                                cardholder=(txns[0].raw_data or {}).get('cardholder', last4),
                                transactions=txns,
                            )
                            for last4, txns in cards_dict.items()
                        ]
                        return TransactionImportPreview(
                            transactions=all_txns,
                            detected_format='pdf',
                            institution='c6',
                            cards=cards_preview,
                        )

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='unsupported_pdf_format',
            )

        elif filename.lower().endswith('.ofx') or filename.lower().endswith('.qfx'):
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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to parse import file: filename=%s, size=%d bytes, "
            "content_type=%s, first_100_bytes=%r, error=%s",
            filename, len(content), file.content_type,
            content[:100], e,
            exc_info=True,
        )
        if is_pdf:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='pdf_parse_error',
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
    account = await account_service.get_account(session, data.account_id, user.id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    imported, skipped, import_log_id = await import_service.import_transactions(
        session, user.id, data.account_id, data.transactions, data.detected_format or "import",
        filename=data.filename, detected_format=data.detected_format,
        detect_duplicates=data.detect_duplicates,
    )

    return {"imported": imported, "skipped": skipped, "import_log_id": str(import_log_id)}
