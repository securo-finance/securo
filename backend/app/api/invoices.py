"""Invoicing ledger routes.

Every route is gated twice: `require_module(INVOICES)` asks whether this
workspace has the module at all, and the write variant additionally asks
whether the member's role may write. A personal workspace gets a 404
from all of them — the same answer it would get for a URL that does not
exist, which is the honest thing to say to a client that should not know
the feature is there.
"""
import uuid
from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.models.workspace import Workspace
from app.core.module_gate import require_module, require_module_write
from app.core.workspace_context import WorkspaceContext
from app.schemas.invoice import (
    AllocationCreate,
    InvoiceCreate,
    InvoiceDirection,
    InvoiceFacets,
    InvoiceRead,
    InvoiceSettingsRead,
    InvoiceSettingsUpdate,
    InvoiceSummary,
    InvoiceUpdate,
    IssuerProfileRead,
    IssuerProfileUpdate,
    ShareLinkRead,
)
from app.services import (
    invoice_attachment_service,
    invoice_logo_service,
    invoice_document,
    invoice_pdf,
    invoice_service,
)
from app.services.invoice_service import InvoiceError
from app.services.module_service import ModuleId

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

read_ctx = require_module(ModuleId.INVOICES)
write_ctx = require_module_write(ModuleId.INVOICES)

#: Every list, count and total is about one side of the ledger. The
#: parameter defaults rather than being required so today's callers keep
#: working unchanged, and so a caller that forgets it gets receivables —
#: the side that exists — instead of both mixed together.
DirectionParam = Query("receivable", description="Which side of the ledger to read")


def _serialize(invoice, today: Optional[_date] = None) -> InvoiceRead:
    """Attach the derived answers to the stored row.

    Computed here, once, from the single definition in the service — so
    the API can expose `state`, `balance` and `days_overdue` without any
    of them ever being written to a column that could drift.
    """
    payload = InvoiceRead.model_validate(invoice, from_attributes=True)
    payload.state = invoice_service.derive_state(invoice, today)
    payload.amount_paid = invoice_service.allocated_total(invoice)
    payload.balance = invoice_service.balance(invoice)
    payload.days_overdue = invoice_service.days_overdue(invoice, today)
    return payload


async def _load(session: AsyncSession, invoice_id: uuid.UUID, workspace_id: uuid.UUID):
    invoice = await invoice_service.get_invoice(session, invoice_id, workspace_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return invoice


def _http(error: InvoiceError) -> HTTPException:
    """Map a ledger rule to a response the frontend can translate."""
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
    )


# ---------------------------------------------------------------------------
# Settings — before /{invoice_id} so the literal path wins the match
# ---------------------------------------------------------------------------
@router.get("/settings", response_model=InvoiceSettingsRead)
async def read_settings(
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    return await invoice_service.get_settings(session, ctx.workspace.id)


@router.patch("/settings", response_model=InvoiceSettingsRead)
async def write_settings(
    payload: InvoiceSettingsUpdate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    settings = await invoice_service.update_settings(
        session, ctx.workspace.id, payload.model_dump(exclude_unset=True)
    )
    await session.commit()
    return settings


@router.get("/facets", response_model=InvoiceFacets)
async def read_facets(
    year: Optional[int] = Query(None, ge=1970, le=2200),
    direction: InvoiceDirection = DirectionParam,
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    """Years that have invoices, and how many are in each state.

    One call for the whole filter bar. The counts use the same derived
    state the list does, so a chip reading 3 opens a list of exactly 3.
    """
    return await invoice_service.facets(session, ctx.workspace.id, year, direction)


@router.get("/summary", response_model=InvoiceSummary)
async def read_summary(
    direction: InvoiceDirection = DirectionParam,
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    data = await invoice_service.aging_summary(
        session, ctx.workspace.id, direction=direction
    )
    data["upcoming"] = [_serialize(inv) for inv in data["upcoming"]]
    return data


# ---------------------------------------------------------------------------
# Issuer identity — the workspace describing itself (T10)
# ---------------------------------------------------------------------------
@router.get("/issuer", response_model=IssuerProfileRead)
async def read_issuer(
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    tax_ids = await invoice_service.get_issuer_tax_ids(session, ctx.workspace.id)
    return IssuerProfileRead(
        legal_name=ctx.workspace.legal_name,
        address=ctx.workspace.address,
        tax_jurisdiction=ctx.workspace.tax_jurisdiction,
        tax_ids=tax_ids,
    )


@router.patch("/issuer", response_model=IssuerProfileRead)
async def write_issuer(
    payload: IssuerProfileUpdate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    # The context's workspace is already attached to this session; fetching
    # it again would only add a query and a `| None` to reason about.
    workspace = ctx.workspace
    data = payload.model_dump(exclude_unset=True)
    # Blankable on purpose: clearing a legal name is a thing people do.
    for field in ("legal_name", "address"):
        if field in data:
            setattr(workspace, field, data[field])
    tax_ids = None
    if data.get("tax_ids") is not None:
        try:
            tax_ids = await invoice_service.set_issuer_tax_ids(
                session, ctx.workspace.id, [dict(t) for t in data["tax_ids"]]
            )
        except InvoiceError as exc:
            raise _http(exc)
    await session.commit()
    if tax_ids is None:
        tax_ids = await invoice_service.get_issuer_tax_ids(session, ctx.workspace.id)
    return IssuerProfileRead(
        legal_name=workspace.legal_name,
        address=workspace.address,
        tax_jurisdiction=workspace.tax_jurisdiction,
        tax_ids=tax_ids,
    )


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
@router.get("", response_model=list[InvoiceRead])
async def list_invoices(
    state: Optional[str] = Query(None, description="Filter by derived state"),
    # Scopes the list only. The summary stays global on purpose: an
    # unpaid invoice from two years ago is still owed today, so
    # "outstanding" has no year.
    year: Optional[int] = Query(None, ge=1970, le=2200, description="Filter by issue year"),
    direction: InvoiceDirection = DirectionParam,
    payee_id: Optional[uuid.UUID] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoices = await invoice_service.list_invoices(
        session, ctx.workspace.id, state=state, year=year, direction=direction,
        payee_id=payee_id, q=q, limit=limit, offset=offset,
    )
    return [_serialize(inv) for inv in invoices]


@router.post("", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    payload: InvoiceCreate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    data = payload.model_dump(exclude_unset=True)
    if data.get("lines") is not None:
        data["lines"] = [dict(line) for line in data["lines"]]
    try:
        invoice = await invoice_service.create_invoice(
            session, ctx.workspace.id, ctx.user_id, data
        )
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    invoice = await _load(session, invoice.id, ctx.workspace.id)
    return _serialize(invoice)


@router.get("/{invoice_id}", response_model=InvoiceRead)
async def get_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.patch("/{invoice_id}", response_model=InvoiceRead)
async def update_invoice(
    invoice_id: uuid.UUID,
    payload: InvoiceUpdate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("lines") is not None:
        data["lines"] = [dict(line) for line in data["lines"]]
    try:
        invoice = await invoice_service.update_invoice(session, invoice, data)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.delete_invoice(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()


# ---------------------------------------------------------------------------
# The decisions. Each is its own route, because a status that changed
# always has a reason, and a PATCH of a string never records one.
# ---------------------------------------------------------------------------
@router.post("/{invoice_id}/issue", response_model=InvoiceRead)
async def issue_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.issue_invoice(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.post("/{invoice_id}/void", response_model=InvoiceRead)
async def void_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.void_invoice(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.post("/{invoice_id}/uncollectible", response_model=InvoiceRead)
async def write_off_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.mark_uncollectible(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.post("/{invoice_id}/reopen", response_model=InvoiceRead)
async def reopen_invoice(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.reopen_invoice(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


# ---------------------------------------------------------------------------
# Allocations — money bound to debt
# ---------------------------------------------------------------------------
@router.post("/{invoice_id}/allocations", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
async def create_allocation(
    invoice_id: uuid.UUID,
    payload: AllocationCreate,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.allocate(session, invoice, payload.transaction_id, payload.amount)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


@router.delete("/{invoice_id}/allocations/{allocation_id}", response_model=InvoiceRead)
async def remove_allocation(
    invoice_id: uuid.UUID,
    allocation_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        await invoice_service.unallocate(session, invoice, allocation_id)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return _serialize(await _load(session, invoice_id, ctx.workspace.id))


# ---------------------------------------------------------------------------
# The rendered document
# ---------------------------------------------------------------------------
async def _document(session: AsyncSession, invoice, workspace: Workspace):
    settings = await invoice_service.get_settings(session, workspace.id)
    return await invoice_document.build_document(session, invoice, settings, workspace)


@router.get("/{invoice_id}/document")
async def read_document(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    """The invoice as a document, resolved.

    The same structure the PDF renderer consumes, so the preview on
    screen and the file the client receives cannot disagree.
    """
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    document = await _document(session, invoice, ctx.workspace)
    payload = invoice_document.document_payload(document)
    # The URL is built by whoever serves the document: this surface is
    # behind a session, the share page is behind a token, and the
    # resolver knows about neither.
    payload["logo_url"] = (
        f"/api/invoices/logo/{payload['logo_id']}" if payload.get("logo_id") else None
    )

    # If a real document was filed, say so. The resolved page is still
    # returned — it is the summary the screen shows around the file — but
    # the reader now knows a page exists that nobody has to redraw.
    primary = await invoice_attachment_service.primary_for(session, invoice.id)
    payload["source_file"] = (
        {
            "id": str(primary.id),
            "filename": primary.filename,
            "content_type": primary.content_type,
        }
        if primary is not None
        else None
    )
    return payload


@router.post("/settings/logo", response_model=InvoiceSettingsRead)
async def upload_logo(
    file: UploadFile,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    """Replace the workspace's mark with an uploaded image."""
    settings = await invoice_service.get_settings(session, ctx.workspace.id)
    try:
        await invoice_logo_service.store(
            session,
            settings,
            ctx.workspace.id,
            await file.read(),
            file.content_type or "application/octet-stream",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    await session.commit()
    await session.refresh(settings)
    return settings


@router.delete("/settings/logo", response_model=InvoiceSettingsRead)
async def remove_logo(
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    settings = await invoice_service.get_settings(session, ctx.workspace.id)
    await invoice_logo_service.clear(session, settings)
    await session.commit()
    await session.refresh(settings)
    return settings


@router.get("/logo/{logo_id}")
async def read_logo(
    logo_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    """One logo by id.

    By id rather than "the current one" because a document issued under
    an older mark froze that id, and asking for the current logo would
    repaint it.
    """
    data = await invoice_logo_service.read(ctx.workspace.id, logo_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logo not found")
    return Response(
        content=data,
        media_type="image/png",
        # Immutable: an id names one file forever, so a client that has
        # it never needs to ask again.
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/{invoice_id}/pdf")
async def download_pdf(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(read_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)

    # When a real document was filed, that file is the answer. Rendering
    # our own page over the top of one we were handed produces something
    # that looks official and is not — and on a supplier's bill it would
    # be a page nobody issued.
    primary = await invoice_attachment_service.primary_for(session, invoice.id)
    if primary is not None:
        data = await invoice_attachment_service.read_bytes(session, primary)
        return Response(
            content=data,
            media_type=primary.content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{primary.filename}"'
            },
        )

    # Nothing filed and we did not write it: there is no document to hand
    # over. Rendering one would produce a page for a document somebody
    # else issued, which is the invention this module exists to stop.
    # Refused here as well as hidden in the UI, because a rule enforced
    # only in a button is a rule the next caller does not know about.
    if invoice.origin == "imported":
        raise _http(
            InvoiceError(
                "no_document_filed",
                "This document was issued elsewhere. Attach the file that was received.",
                status_code=status.HTTP_409_CONFLICT,
            )
        )

    document = await _document(session, invoice, ctx.workspace)
    # Loaded here rather than inside the renderer, which does no I/O on
    # purpose. Until now nothing passed it at all, so a workspace with a
    # logo saw it on screen and got a file without one.
    logo_bytes = (
        await invoice_logo_service.read(ctx.workspace.id, uuid.UUID(document.logo_id))
        if document.logo_id
        else None
    )
    pdf = invoice_pdf.render_pdf(document, logo_bytes)
    filename = f"{document.number or 'draft'}.pdf".replace("/", "-")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{invoice_id}/share", response_model=ShareLinkRead, status_code=status.HTTP_201_CREATED)
async def create_share_link(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    try:
        token = await invoice_service.create_share_token(session, invoice)
    except InvoiceError as exc:
        raise _http(exc)
    await session.commit()
    return ShareLinkRead(token=token, path=f"/i/{token}")


@router.delete("/{invoice_id}/share", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share_link(
    invoice_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(write_ctx),
    session: AsyncSession = Depends(get_async_session),
):
    invoice = await _load(session, invoice_id, ctx.workspace.id)
    await invoice_service.revoke_share_token(session, invoice)
    await session.commit()
