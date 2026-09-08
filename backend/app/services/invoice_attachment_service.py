"""Gathering paper under an invoice.

Mirrors `attachment_service` — same validation, same storage provider,
same sanitised filenames — and adds the two things an invoice needs that
a transaction does not: the role a file plays, and which file *is* the
document.
"""
import uuid
from datetime import date as _date
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.invoice import Invoice
from app.models.invoice_attachment import ATTACHMENT_KINDS, InvoiceAttachment
from app.providers import get_storage_provider
from app.services.attachment_service import _validate_file, sanitize_filename


async def _invoice_in_workspace(
    session: AsyncSession, invoice_id: uuid.UUID, workspace_id: uuid.UUID
) -> Invoice:
    result = await session.execute(
        select(Invoice).where(
            Invoice.id == invoice_id, Invoice.workspace_id == workspace_id
        )
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise LookupError("Invoice not found")
    return invoice


async def _clear_primary(session: AsyncSession, invoice_id: uuid.UUID) -> None:
    """Demote whatever currently holds the flag.

    Done before setting the new one because the partial unique index
    admits exactly one true row per invoice, and a second insert would
    otherwise be rejected instead of taking over.
    """
    result = await session.execute(
        select(InvoiceAttachment).where(
            InvoiceAttachment.invoice_id == invoice_id,
            InvoiceAttachment.is_primary.is_(True),
        )
    )
    for row in result.scalars().all():
        row.is_primary = False
    await session.flush()


async def find_by_external_id(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    source: str,
    external_id: str,
) -> Optional[InvoiceAttachment]:
    """The row a previous sync of the same file already wrote, if any.

    What keeps a second run of an integration from filing the same fiscal
    document twice under one invoice.
    """
    result = await session.execute(
        select(InvoiceAttachment).where(
            InvoiceAttachment.workspace_id == workspace_id,
            InvoiceAttachment.source == source,
            InvoiceAttachment.external_id == external_id,
        )
    )
    return result.scalar_one_or_none()


async def upload(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    invoice_id: uuid.UUID,
    filename: str,
    content_type: str,
    data: bytes,
    kind: str = "other",
    source: Optional[str] = None,
    external_id: Optional[str] = None,
    document_number: Optional[str] = None,
    issued_at: Optional[_date] = None,
    is_primary: Optional[bool] = None,
) -> InvoiceAttachment:
    if kind not in ATTACHMENT_KINDS:
        raise ValueError(f"Unknown document kind '{kind}'.")

    filename = sanitize_filename(filename)
    _validate_file(filename, content_type, len(data))
    invoice = await _invoice_in_workspace(session, invoice_id, workspace_id)

    settings = get_settings()
    count = (
        await session.execute(
            select(func.count()).where(InvoiceAttachment.invoice_id == invoice_id)
        )
    ).scalar_one()
    if count >= settings.storage_max_attachments_per_invoice:
        raise ValueError(
            f"Maximum of {settings.storage_max_attachments_per_invoice} documents per invoice reached."
        )

    # The first bill filed against a document we did not write is the
    # document. Nobody should have to tell us that the supplier's own PDF
    # outranks a page we would have drawn from our own fields, and the
    # flag stays explicit so it can still be moved by hand afterwards.
    if is_primary is None:
        is_primary = (
            kind == "bill" and invoice.origin == "imported" and not invoice.attachments
        )

    prefix = uuid.uuid4().hex[:8]
    storage_key = f"{workspace_id}/invoices/{invoice_id}/{prefix}_{filename}"

    # Checked before the bytes are written. `source` + `external_id` is
    # unique per workspace, so a second sync of the same file is rejected
    # by the index — and if that happened after the upload, the blob it
    # had just written would stay in storage with no row pointing at it
    # and nothing to find it by.
    if source and external_id:
        existing = await find_by_external_id(session, workspace_id, source, external_id)
        if existing is not None:
            return existing

    storage = get_storage_provider()
    stored = await storage.upload(storage_key, data, content_type)

    if is_primary:
        await _clear_primary(session, invoice_id)

    attachment = InvoiceAttachment(
        invoice_id=invoice_id,
        workspace_id=workspace_id,
        user_id=user_id,
        source=(source or None),
        external_id=(external_id or None),
        kind=kind,
        is_primary=bool(is_primary),
        document_number=(document_number or None),
        issued_at=issued_at,
        filename=filename,
        storage_key=stored.storage_key,
        content_type=stored.content_type,
        size=stored.size,
    )
    session.add(attachment)
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race with a concurrent sync of the same file. The row is
        # not ours, so neither is the blob: leaving it would be an orphan
        # nothing can reach.
        await session.rollback()
        try:
            await storage.delete(stored.storage_key)
        except Exception:
            pass
        existing = (
            await find_by_external_id(session, workspace_id, source, external_id)
            if source and external_id
            else None
        )
        if existing is not None:
            return existing
        raise
    await session.refresh(attachment)
    return attachment


async def listing(
    session: AsyncSession, workspace_id: uuid.UUID, invoice_id: uuid.UUID
) -> list[InvoiceAttachment]:
    await _invoice_in_workspace(session, invoice_id, workspace_id)
    result = await session.execute(
        select(InvoiceAttachment)
        .where(InvoiceAttachment.invoice_id == invoice_id)
        .order_by(InvoiceAttachment.is_primary.desc(), InvoiceAttachment.created_at)
    )
    return list(result.scalars().all())


async def primary_for(
    session: AsyncSession, invoice_id: uuid.UUID
) -> Optional[InvoiceAttachment]:
    """The file that *is* the document, if one was filed."""
    result = await session.execute(
        select(InvoiceAttachment).where(
            InvoiceAttachment.invoice_id == invoice_id,
            InvoiceAttachment.is_primary.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def _get(
    session: AsyncSession, attachment_id: uuid.UUID, workspace_id: uuid.UUID
) -> InvoiceAttachment:
    result = await session.execute(
        select(InvoiceAttachment).where(
            InvoiceAttachment.id == attachment_id,
            InvoiceAttachment.workspace_id == workspace_id,
        )
    )
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise LookupError("Attachment not found")
    return attachment


async def download(
    session: AsyncSession, attachment_id: uuid.UUID, workspace_id: uuid.UUID
) -> tuple[InvoiceAttachment, bytes]:
    attachment = await _get(session, attachment_id, workspace_id)
    storage = get_storage_provider()
    return attachment, await storage.download(attachment.storage_key)


async def read_bytes(session: AsyncSession, attachment: InvoiceAttachment) -> bytes:
    storage = get_storage_provider()
    return await storage.download(attachment.storage_key)


async def update(
    session: AsyncSession,
    attachment_id: uuid.UUID,
    workspace_id: uuid.UUID,
    data: dict,
) -> InvoiceAttachment:
    attachment = await _get(session, attachment_id, workspace_id)

    if "kind" in data and data["kind"] is not None:
        if data["kind"] not in ATTACHMENT_KINDS:
            raise ValueError(f"Unknown document kind '{data['kind']}'.")
        attachment.kind = data["kind"]
    if "source" in data:
        attachment.source = data["source"] or None
    if "document_number" in data:
        attachment.document_number = data["document_number"] or None
    if "issued_at" in data:
        attachment.issued_at = data["issued_at"]
    if data.get("is_primary") is True:
        await _clear_primary(session, attachment.invoice_id)
        attachment.is_primary = True
    elif data.get("is_primary") is False:
        attachment.is_primary = False

    await session.commit()
    await session.refresh(attachment)
    return attachment


async def delete(
    session: AsyncSession, attachment_id: uuid.UUID, workspace_id: uuid.UUID
) -> None:
    attachment = await _get(session, attachment_id, workspace_id)
    storage = get_storage_provider()
    await storage.delete(attachment.storage_key)
    await session.delete(attachment)
    await session.commit()


async def cleanup_files(session: AsyncSession, invoice_id: uuid.UUID) -> None:
    """Remove the stored files for an invoice about to be deleted.

    The rows go with the invoice through the cascade; the files have to be
    asked for, and are best-effort — one already gone must not block a
    delete the user asked for.
    """
    result = await session.execute(
        select(InvoiceAttachment.storage_key).where(
            InvoiceAttachment.invoice_id == invoice_id
        )
    )
    keys = [row[0] for row in result.all()]
    if not keys:
        return
    storage = get_storage_provider()
    for key in keys:
        try:
            await storage.delete(key)
        except Exception:
            pass
