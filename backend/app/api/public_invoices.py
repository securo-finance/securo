"""The shared invoice: one document, reachable by its link alone.

This is the only unauthenticated surface in the invoicing module, so the
rules it plays by are worth stating plainly:

  - **The token is the credential.** It is 32 random bytes, never derived
    from the invoice id, and it is the entire scope of the request. There
    is no workspace header, no session, and nothing to enumerate.
  - **It serves the document and only the document.** The response is the
    same resolved structure the owner sees on the invoice page — issuer,
    client, lines, totals. Internal notes, allocations, the payee record
    and every other workspace object stay behind the authenticated API.
  - **A revoked or voided invoice is indistinguishable from one that
    never existed.** Both answer 404, because "this used to be here" is
    itself information.

No rate limiting is added here beyond the app's own: the token is not
guessable, so there is nothing to brute-force at a rate that matters.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models.workspace import Workspace
from app.services import (
    invoice_document,
    invoice_logo_service,
    invoice_pdf,
    invoice_service,
)

router = APIRouter(prefix="/api/public/invoices", tags=["invoices"])


async def _resolve(session: AsyncSession, token: str):
    invoice = await invoice_service.get_invoice_by_share_token(session, token)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    workspace = await session.get(Workspace, invoice.workspace_id)
    if workspace is None:
        # Unreachable in practice — the invoice cascades with its
        # workspace — but a document with no issuer is not a document.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    settings = await invoice_service.get_settings(session, invoice.workspace_id)
    return invoice, await invoice_document.build_document(
        session, invoice, settings, workspace
    )


@router.get("/{token}")
async def read_shared_invoice(
    token: str, session: AsyncSession = Depends(get_async_session)
):
    _, document = await _resolve(session, token)
    payload = invoice_document.document_payload(document)
    # Reached through the token, like everything else here. The logo id
    # is dropped from the response: a client needs the picture, not the
    # workspace's internal handle for it.
    payload["logo_url"] = f"/api/public/invoices/{token}/logo" if payload.pop("logo_id", None) else None
    return payload


@router.get("/{token}/logo")
async def read_shared_logo(
    token: str, session: AsyncSession = Depends(get_async_session)
):
    """The mark on the shared document.

    Scoped by the same token as everything else, and by the id frozen in
    the invoice's snapshot rather than the workspace's current logo — a
    document a client is holding does not change its letterhead because
    the issuer rebranded.
    """
    invoice, document = await _resolve(session, token)
    data = (
        await invoice_logo_service.read(invoice.workspace_id, uuid.UUID(document.logo_id))
        if document.logo_id
        else None
    )
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/{token}/pdf")
async def download_shared_pdf(
    token: str, session: AsyncSession = Depends(get_async_session)
):
    invoice, document = await _resolve(session, token)
    logo_bytes = (
        await invoice_logo_service.read(invoice.workspace_id, uuid.UUID(document.logo_id))
        if document.logo_id
        else None
    )
    filename = f"{document.number or 'invoice'}.pdf".replace("/", "-")
    return Response(
        content=invoice_pdf.render_pdf(document, logo_bytes),
        media_type="application/pdf",
        # `inline` rather than `attachment`: someone opening a link
        # expects to see the document, not to receive a download.
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
