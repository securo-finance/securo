import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.money import MoneyOutOfRange
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.transaction import TransactionImportPreview, TransactionImportRequest
from app.services import account_service, import_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transactions", tags=["import"])


@router.post("/import/preview", response_model=TransactionImportPreview)
async def preview_import(
    file: UploadFile = File(...),
    date_format: Optional[str] = Form(None),
    flip_amount: bool = Form(False),
    inflow_column: Optional[str] = Form(None),
    outflow_column: Optional[str] = Form(None),
    column_mapping: Optional[str] = Form(None),
    # Read-gated on purpose, and the exception is deliberate rather than an
    # oversight. This is a POST because it takes a file upload, not because
    # it changes anything: it parses the upload and returns what *would* be
    # imported. The only workspace data it touches is
    # `enrich_with_category_suggestions`, which SELECTs rules and categories
    # to label the preview. Nothing is persisted, so a read-only member
    # previewing a file is doing exactly what their role allows. The write
    # gate belongs on `POST /import` below, which is where the rows land.
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    content = await file.read()
    filename = file.filename or ""

    # A PDF or a photo is not a format this parser reads. Without this, the
    # fallback below took a PDF for an empty QIF and answered 0 rows, no error.
    if import_service.is_document_file(filename, content):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "PDF and image files are not statement formats. Export the statement as OFX, CSV, QIF "
                "or CAMT, or turn on document reading (an LLM connection under Agents) to read it with a model."
            ),
        )

    logger.info(
        "Import preview requested: filename=%s, size=%d bytes, content_type=%s",
        filename, len(content), file.content_type,
    )

    # column_mapping arrives as a JSON-encoded form field (Securo field -> CSV header)
    parsed_mapping: Optional[dict] = None
    if column_mapping:
        try:
            parsed_mapping = json.loads(column_mapping)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid column_mapping: must be a JSON object",
            )
        if not isinstance(parsed_mapping, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid column_mapping: must be a JSON object",
            )

    parse_error: Optional[str] = None
    failed_rows = []
    try:
        if filename.lower().endswith('.ofx') or filename.lower().endswith('.qfx'):
            transactions = import_service.parse_ofx(content)
            detected_format = "ofx"
        elif filename.lower().endswith('.qif'):
            transactions, failed_rows = import_service.parse_qif(content, date_format=date_format)
            detected_format = "qif"
        elif filename.lower().endswith('.xml') or filename.lower().endswith('.camt'):
            transactions, failed_rows = import_service.parse_camt(content)
            detected_format = "camt"
        elif filename.lower().endswith('.csv'):
            detected_format = "csv"
            try:
                transactions, failed_rows = import_service.parse_csv(
                    content,
                    date_format=date_format,
                    flip_amount=flip_amount,
                    inflow_column=inflow_column,
                    outflow_column=outflow_column,
                    column_mapping=parsed_mapping,
                )
            except ValueError as csv_err:
                # The CSV's columns couldn't be auto-mapped. As long as we can
                # still read its headers, return a soft failure so the UI can
                # show the column-mapping dropdowns instead of a hard error.
                if not import_service.detect_csv_columns(content):
                    raise
                transactions = []
                parse_error = str(csv_err)
        else:
            # Try to detect format
            try:
                transactions = import_service.parse_ofx(content)
                detected_format = "ofx"
            except Exception:
                try:
                    transactions, failed_rows = import_service.parse_qif(content, date_format=date_format)
                    detected_format = "qif"
                except Exception:
                    try:
                        transactions, failed_rows = import_service.parse_camt(content)
                        detected_format = "camt"
                    except Exception:
                        transactions, failed_rows = import_service.parse_csv(content)
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

    transactions = await import_service.enrich_with_category_suggestions(
        session, ctx.workspace.id, transactions,
    )

    # Expose CSV headers so the UI can offer accurate column-mapping dropdowns.
    csv_columns: list[str] = []
    if detected_format == "csv":
        try:
            csv_columns = import_service.detect_csv_columns(content)
        except Exception:
            csv_columns = []

    return TransactionImportPreview(
        transactions=transactions,
        detected_format=detected_format,
        csv_columns=csv_columns,
        parse_error=parse_error,
        failed_rows=failed_rows,
    )


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_transactions(
    data: TransactionImportRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    # Verify the target account lives in this workspace BEFORE doing any
    # writes — otherwise a hand-rolled request could import into an
    # account owned by another tenant.
    account = await account_service.get_account(session, data.account_id, ctx.workspace.id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    if data.document_extraction_id is not None:
        from app.services import document_ai_settings_service, document_extraction_service

        if not document_ai_settings_service.document_ai_available():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document reading is not enabled")
        try:
            await document_extraction_service.require_statement_ready(
                session, ctx.workspace.id, data.document_extraction_id,
            )
        except document_extraction_service.ExtractionError as exc:
            raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})

    before_commit = None
    if data.document_extraction_id is not None:
        extraction_id = data.document_extraction_id

        async def before_commit(import_log_id):
            # Same commit as the rows: the read is marked imported only if
            # they landed, and they land only with it marked.
            await document_extraction_service.mark_statement_confirmed(
                session,
                workspace_id=ctx.workspace.id,
                user_id=ctx.user_id,
                extraction_id=extraction_id,
                account_id=data.account_id,
                import_log_id=import_log_id,
                rows=data.transactions,
                commit=False,
            )

    try:
        imported, skipped, excluded, import_log_id = await import_service.import_transactions(
            session, ctx.workspace.id, ctx.user_id, data.account_id, data.transactions, "import",
            filename=data.filename, detected_format=data.detected_format,
            detect_duplicates=data.detect_duplicates,
            before_commit=before_commit,
        )
    except MoneyOutOfRange as e:
        # A row whose conversion does not fit the column (issue #422): the
        # import commits once at the end, so nothing of it was saved.
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        # Another import of the same document won the race: nothing of this
        # one was committed; drop it and say so.
        from app.services.document_extraction_service import ExtractionError

        if not isinstance(e, ExtractionError):
            raise
        await session.rollback()
        raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})

    return {
        "imported": imported,
        "skipped": skipped,
        "excluded": excluded,
        "import_log_id": str(import_log_id),
    }
