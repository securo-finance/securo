import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.transaction import (
    DryRunSummary,
    PresetCategorySuggestion,
    TransactionImportPreview,
    TransactionImportRequest,
)
from app.services import account_service, import_service
from app.services.import_category_mapping import MonarchCategoryMapper
from app.services.import_presets import MONARCH

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transactions", tags=["import"])

# Preset vocabulary: auto/generic stay generic; Monarch is explicit confirmation.
_PRESET_CHOICES = ("", "auto", "monarch", "generic")


@router.post("/import/preview", response_model=TransactionImportPreview)
async def preview_import(
    file: UploadFile = File(...),
    date_format: Optional[str] = Form(None),
    flip_amount: Optional[bool] = Form(None),
    inflow_column: Optional[str] = Form(None),
    outflow_column: Optional[str] = Form(None),
    column_mapping: Optional[str] = Form(None),
    # Source-preset selector: absent/"auto" and "generic" use plain CSV.
    # Only "monarch" explicitly confirms its sign/date defaults; headers
    # cannot establish provenance. This endpoint remains read-gated.
    preset: Optional[str] = Form(None),
    # Dry-run summary (§7.3): read-only counts via the extracted
    # find_existing_duplicate; requires the target account_id. Writes
    # remain the job of POST /import below.
    dry_run: bool = Form(False),
    account_id: Optional[str] = Form(None),
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

    logger.info(
        "Import preview requested: filename=%s, size=%d bytes, content_type=%s",
        filename, len(content), file.content_type,
    )

    requested_preset = (preset or "").strip().lower()
    if requested_preset not in _PRESET_CHOICES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown preset: {preset!r}. Expected one of: auto, monarch, generic",
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
    # Resolved preset actually applied to this parse (None = generic path).
    applied_preset: Optional[str] = None
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
            # Only an explicit source selection confirms Monarch provenance.
            # Ordinary headers cannot justify changing sign/date semantics.
            preset_spec = MONARCH if requested_preset == "monarch" else None
            applied_preset = preset_spec.name if preset_spec else None
            try:
                transactions, failed_rows = import_service.parse_csv(
                    content,
                    date_format=date_format,
                    flip_amount=flip_amount,
                    inflow_column=inflow_column,
                    outflow_column=outflow_column,
                    column_mapping=parsed_mapping,
                    preset=preset_spec,
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

    # Preset category mapping (§4.2/§4.3): one mapper serves the mapping
    # card suggestions and the dry-run unmapped count.
    mapper = None
    preset_suggestions: list[PresetCategorySuggestion] = []
    distinct_values: list[str] = []
    if applied_preset:
        mapper = await MonarchCategoryMapper.load(session, ctx.workspace.id)
        counts: dict[str, int] = {}
        for txn in transactions:
            value = (txn.category_name or "").strip()
            if not value:
                continue
            if value not in counts:
                counts[value] = 0
                distinct_values.append(value)
            counts[value] += 1
        for value in distinct_values:
            target = mapper.suggest(value)
            preset_suggestions.append(PresetCategorySuggestion(
                value=value,
                row_count=counts[value],
                suggested_category_id=target.id if target else None,
                suggested_category_name=target.name if target else None,
            ))

    # Dry-run summary (§7.3): read-only pre-flight counts. Validates the
    # target account, then runs the same extracted duplicate lookup the
    # write path uses — one SELECT per row, no writes (asserted by test).
    dry_run_summary: Optional[DryRunSummary] = None
    if dry_run:
        if not account_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="dry_run requires account_id",
            )
        try:
            account_uuid = uuid.UUID(account_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid account_id",
            )
        account = await account_service.get_account(session, account_uuid, ctx.workspace.id)
        if not account:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

        matched_ids: set[uuid.UUID] = set()
        would_skip = 0
        for txn in transactions:
            duplicate = await import_service.find_existing_duplicate(
                session, account.id, txn, matched_ids=matched_ids,
            )
            if duplicate:
                matched_ids.add(duplicate.id)
                would_skip += 1

        unmapped_categories = sum(
            1 for value in distinct_values if mapper is None or mapper.suggest(value) is None
        )

        dry_run_summary = DryRunSummary(
            rows_in=len(transactions) + len(failed_rows),
            would_import=len(transactions) - would_skip,
            would_skip_dupes=would_skip,
            failed_rows=len(failed_rows),
            unmapped_categories=unmapped_categories,
        )

    return TransactionImportPreview(
        transactions=transactions,
        detected_format=detected_format,
        csv_columns=csv_columns,
        parse_error=parse_error,
        failed_rows=failed_rows,
        preset=applied_preset,
        dry_run=dry_run_summary,
        preset_category_suggestions=preset_suggestions,
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
        # This rejection occurs before the import service is invoked. It is
        # the only retry evidence this route promises; a later exception may
        # have occurred during/after commit and must remain outcome-unknown.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found",
            headers={"X-Import-Outcome": "not-committed"},
        )

    imported, skipped, excluded, import_log_id = await import_service.import_transactions(
        session, ctx.workspace.id, ctx.user_id, data.account_id, data.transactions, "import",
        filename=data.filename, detected_format=data.detected_format,
        detect_duplicates=data.detect_duplicates,
    )

    return {
        "imported": imported,
        "skipped": skipped,
        "excluded": excluded,
        "import_log_id": str(import_log_id),
    }
