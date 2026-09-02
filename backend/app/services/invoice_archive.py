"""Filing the document that was issued, at the moment it is issued.

## The problem this fixes

`/pdf` renders the invoice live, and the renderer prints *Paid* and
*Balance* as soon as money moves against it. So an invoice sent showing
"Total 10.000" came back, after a 5.000 payment, showing a balance of
5.000: a different document at the same address, including the public
link the client was given.

That is the same failure the numbering rules exist to prevent: **renaming
a document somebody already has in their hands.**

The fix is not to make the renderer forget about payments. Two different
documents were coming out of one door:

  - **The invoice as issued** is a record of what was communicated. It
    must be reproducible forever.
  - **A statement of account** says what is paid and what is left. It is
    supposed to change, and it is a different thing to ask for.

## Why filing, rather than a flag on the renderer

The architecture already answers this and was not being used. An invoice
gathers paper (`invoice_attachments`), and one file can be marked as
*the* document, which `/pdf` already prefers over anything it could
render. A file cannot drift. So issuing files the PDF, and from that
moment the answer to "what did we send" is a stored artifact rather than
a re-rendering that depends on what has happened since.

Nothing new has to be remembered, and no second code path can forget to
freeze anything: it is frozen because it is a file.

## What it deliberately does not do

**An imported document is never filed this way.** We did not write it,
and rendering our own page over someone else's bill produces something
that looks official and is not; the same reason `/pdf` refuses to render
for them at all.

**Failing to file never fails the issue.** Issuing an invoice is the act
that matters; the archive is a courtesy to your future self. A storage
error must not leave a workspace unable to bill.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.workspace import Workspace
from app.services import (
    invoice_attachment_service,
    invoice_document,
    invoice_logo_service,
    invoice_pdf,
    invoice_service,
)

logger = logging.getLogger(__name__)

#: Marks the file as one we produced rather than one that was handed to
#: us, so a later importer can tell them apart without guessing from the
#: filename.
SOURCE = "issued"


async def file_issued_document(
    session: AsyncSession,
    invoice: Invoice,
    workspace: Workspace,
    user_id: Optional[uuid.UUID] = None,
) -> None:
    """Render the invoice as issued and keep it.

    Called right after issuing, while the figures still say what was sent.
    Silent about everything that would make it a nuisance: an imported
    document, an invoice that already has a filed document, or a storage
    failure.
    """
    if invoice.origin == "imported":
        return
    if invoice.number is None:
        # Not actually issued. Nothing to preserve, and a draft's numbers
        # are still moving.
        return

    # Somebody already filed the real thing: a signed copy, the version
    # that went by email. Ours would be a second opinion about a document
    # that has an original.
    if await invoice_attachment_service.primary_for(session, invoice.id) is not None:
        return

    try:
        settings = await invoice_service.get_settings(session, workspace.id)
        document = await invoice_document.build_document(
            session, invoice, settings, workspace
        )
        logo = (
            await invoice_logo_service.read(workspace.id, uuid.UUID(document.logo_id))
            if document.logo_id
            else None
        )
        pdf = invoice_pdf.render_pdf(document, logo)
        name = f"{document.number or invoice.number}.pdf".replace("/", "-")

        await invoice_attachment_service.upload(
            session,
            workspace.id,
            user_id,
            invoice.id,
            filename=name,
            content_type="application/pdf",
            data=pdf,
            kind="bill",
            source=SOURCE,
            # Unique per workspace, so re-issuing or a retry converges on
            # one row instead of filing the same page twice.
            external_id=f"{SOURCE}:{invoice.id}",
            document_number=document.number,
            issued_at=invoice.issue_date,
            is_primary=True,
        )
    except Exception:  # noqa: BLE001 - see the module docstring
        # Issuing is the act that matters. A storage error must not leave
        # a workspace unable to bill, so this is logged and swallowed:
        # the invoice is issued either way, and `/pdf` falls back to
        # rendering exactly as it did before.
        logger.warning(
            "Could not file the issued document for invoice %s", invoice.id,
            exc_info=True,
        )
