"""Reading documents into transactions (ADR 0037).

Part of the agents module: every route answers 404 while AGENTS_ENABLED is
off. A workspace reads with one of the agents LLM connections, picked by an
owner (`/settings`); until then an upload is refused as not configured. An
upload stores the file and queues the read; the client polls the extraction
until it is done, then confirms through the import (statements) or the
receipt routes.
"""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace, current_writable_workspace
from app.models.document_extraction import DocumentExtraction
from app.schemas.document_extraction import (
    AttachReceiptRequest,
    BalanceCheck,
    DocumentAiSettingsRead,
    DocumentAiSettingsUpdate,
    DocumentAiStatus,
    DocumentExtractionRead,
    ReceiptMatch,
)
from app.schemas.transaction import TransactionCreate
from app.services import document_ai_settings_service as ai_settings
from app.services import document_extraction_service as extraction_service
from app.services import import_service
from app.services.document_extraction_service import ExtractionError


def _document_ai_available() -> None:
    if not ai_settings.document_ai_available():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document reading is not enabled")


router = APIRouter(prefix="/api/document-ai", tags=["document-ai"], dependencies=[Depends(_document_ai_available)])


def _refuse(exc: ExtractionError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": exc.message})


async def _dispatch(session: AsyncSession, extraction: DocumentExtraction) -> DocumentExtraction:
    """Queue the read. If the queue cannot take it, the read is failed with a
    code the client can retry from, never left pending with no job."""
    from app.tasks.document_tasks import extract_document

    try:
        extract_document.delay(str(extraction.id))
    except Exception as exc:  # broker down past Celery's own publish retries
        return await extraction_service.mark_unqueued(session, extraction, exc)
    return extraction


async def _read(
    session: AsyncSession, ctx: WorkspaceContext, extraction: DocumentExtraction
) -> DocumentExtractionRead:
    out = DocumentExtractionRead.model_validate(extraction, from_attributes=True)
    if extraction.confirmed and "attached" in extraction.confirmed:
        out.attached = bool(extraction.confirmed["attached"])
    if extraction.status != "done" or not extraction.result:
        return out
    result = extraction.result
    if extraction.kind == "statement":
        rows = extraction_service.statement_rows(extraction)
        out.transactions = await import_service.enrich_with_category_suggestions(session, ctx.workspace.id, rows)
        out.statement = result.get("statement")
        check = result.get("balance_check")
        out.balance_check = BalanceCheck.model_validate(check) if check else None
        out.dropped = int(result.get("dropped") or 0)
        out.truncated = bool(result.get("truncated"))
        out.unread_pages = [int(n) for n in result.get("unread_pages") or []]
        out.suggested_account_id = await extraction_service.suggested_account_id(session, extraction)
    else:
        out.receipt = {k: v for k, v in result.items() if k not in ("usage", "unread_pages")}
        out.unread_pages = [int(n) for n in result.get("unread_pages") or []]
        if extraction.confirmed_at is None:
            matches = await extraction_service.receipt_matches(session, ctx.workspace.id, extraction)
            out.matches = [ReceiptMatch.model_validate(m) for m in matches]
            out.suggested_category_id = await extraction_service.receipt_category(session, ctx.workspace.id, extraction)
    return out


@router.get("/status", response_model=DocumentAiStatus)
async def get_status(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    settings = get_settings()
    resolved = await ai_settings.resolve_model(session, ctx.workspace.id)
    answer = await ai_settings.vision_for(resolved) if resolved.configured else None
    return DocumentAiStatus(
        enabled=resolved.configured,
        configured=resolved.configured,
        connection_id=resolved.connection_id,
        connection_name=resolved.connection_name,
        provider=resolved.provider or None,
        model=resolved.model or None,
        vision=answer.vision if answer else "unknown",
        vision_source=answer.source if answer else "none",
        max_file_size_mb=settings.document_ai_max_file_size_mb,
        max_pages=settings.document_ai_max_pages,
    )


@router.get("/settings", response_model=DocumentAiSettingsRead)
async def get_workspace_settings(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_owner()
    row = await ai_settings.get_workspace_settings(session, ctx.workspace.id)
    return DocumentAiSettingsRead(
        connection_id=row.connection_id if row else None,
        model=row.model if row else None,
        vision=row.vision if row else "auto",
    )


@router.put("/settings", response_model=DocumentAiSettingsRead)
async def update_workspace_settings(
    data: DocumentAiSettingsUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_owner()
    try:
        await ai_settings.save_workspace_settings(
            session, ctx.workspace.id, ctx.user_id,
            connection_id=data.connection_id, model=data.model, vision=data.vision,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return await get_workspace_settings(ctx, session)


@router.delete("/settings", status_code=status.HTTP_204_NO_CONTENT)
async def reset_workspace_settings(
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    ctx.require_owner()
    await ai_settings.delete_workspace_settings(session, ctx.workspace.id)


@router.post("/extractions", response_model=DocumentExtractionRead, status_code=status.HTTP_201_CREATED)
async def create_extraction(
    file: UploadFile = File(...),
    kind: str = Form(...),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    content = await file.read()
    try:
        extraction = await extraction_service.create_extraction(
            session, ctx.workspace.id, ctx.user_id, kind=kind, filename=file.filename or "document", content=content,
        )
    except ExtractionError as exc:
        raise _refuse(exc)
    extraction = await _dispatch(session, extraction)
    return await _read(session, ctx, extraction)


async def _get_or_404(session: AsyncSession, ctx: WorkspaceContext, extraction_id: uuid.UUID) -> DocumentExtraction:
    extraction = await extraction_service.get_extraction(session, ctx.workspace.id, extraction_id)
    if extraction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return extraction


@router.get("/extractions/{extraction_id}", response_model=DocumentExtractionRead)
async def get_extraction(
    extraction_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await _read(session, ctx, await _get_or_404(session, ctx, extraction_id))


@router.post("/extractions/{extraction_id}/retry", response_model=DocumentExtractionRead)
async def retry_extraction(
    extraction_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    extraction = await _get_or_404(session, ctx, extraction_id)
    try:
        extraction = await extraction_service.reset_for_retry(session, extraction)
    except ExtractionError as exc:
        raise _refuse(exc)
    extraction = await _dispatch(session, extraction)
    return await _read(session, ctx, extraction)


@router.delete("/extractions/{extraction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_extraction(
    extraction_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    extraction = await _get_or_404(session, ctx, extraction_id)
    try:
        await extraction_service.delete_extraction(session, extraction)
    except ExtractionError as exc:
        raise _refuse(exc)


@router.post("/extractions/{extraction_id}/attach", response_model=DocumentExtractionRead)
async def attach_receipt(
    extraction_id: uuid.UUID,
    data: AttachReceiptRequest,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        extraction = await extraction_service.attach_receipt(
            session, ctx.workspace.id, ctx.user_id, extraction_id, data.transaction_id,
        )
    except ExtractionError as exc:
        raise _refuse(exc)
    return await _read(session, ctx, extraction)


@router.post("/extractions/{extraction_id}/transaction", response_model=DocumentExtractionRead)
async def create_transaction_from_receipt(
    extraction_id: uuid.UUID,
    data: TransactionCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        extraction, _ = await extraction_service.create_transaction_from_receipt(
            session, ctx.workspace.id, ctx.user_id, extraction_id, data,
        )
    except ExtractionError as exc:
        raise _refuse(exc)
    return await _read(session, ctx, extraction)
