"""The files gathered under an invoice.

Kept in its own router rather than folded into `invoices.py` because it
is the transaction-attachment shape verbatim — multipart in, bytes out —
and mixing that with a JSON resource makes both harder to read.
"""
import uuid
from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.module_gate import require_module, require_module_write
from app.services.module_service import ModuleId
from app.core.workspace_context import WorkspaceContext
from app.schemas.invoice import InvoiceAttachmentRead, InvoiceAttachmentUpdate
from app.services import invoice_attachment_service

router = APIRouter(
    prefix="/api/invoices/{invoice_id}/attachments", tags=["invoices"]
)

_read = require_module(ModuleId.INVOICES)
_write = require_module_write(ModuleId.INVOICES)


@router.post("", response_model=InvoiceAttachmentRead, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    invoice_id: uuid.UUID,
    file: UploadFile,
    kind: str = Form("other"),
    #: Which system produced the file. Omitted when a person uploads one.
    source: Optional[str] = Form(None),
    external_id: Optional[str] = Form(None),
    document_number: Optional[str] = Form(None),
    issued_at: Optional[_date] = Form(None),
    is_primary: Optional[bool] = Form(None),
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await invoice_attachment_service.upload(
            session=session,
            workspace_id=ctx.workspace.id,
            user_id=ctx.user_id,
            invoice_id=invoice_id,
            filename=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            data=await file.read(),
            kind=kind,
            source=source,
            external_id=external_id,
            document_number=document_number,
            issued_at=issued_at,
            is_primary=is_primary,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", response_model=list[InvoiceAttachmentRead])
async def list_attachments(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(_read),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await invoice_attachment_service.listing(session, ctx.workspace.id, invoice_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")


@router.get("/{attachment_id}")
async def download_attachment(
    invoice_id: uuid.UUID,
    attachment_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(_read),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        attachment, data = await invoice_attachment_service.download(
            session, attachment_id, ctx.workspace.id
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    return Response(
        content=data,
        media_type=attachment.content_type,
        headers={"Content-Disposition": f'inline; filename="{attachment.filename}"'},
    )


@router.patch("/{attachment_id}", response_model=InvoiceAttachmentRead)
async def update_attachment(
    invoice_id: uuid.UUID,
    attachment_id: uuid.UUID,
    body: InvoiceAttachmentUpdate,
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await invoice_attachment_service.update(
            session, attachment_id, ctx.workspace.id, body.model_dump(exclude_unset=True)
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    invoice_id: uuid.UUID,
    attachment_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(_write),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        await invoice_attachment_service.delete(session, attachment_id, ctx.workspace.id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
