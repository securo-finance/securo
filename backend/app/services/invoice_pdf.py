"""Lay out an `InvoiceDocument` as a PDF.

This module decides nothing about content. Everything it prints came
from `invoice_document.build_document`, which is the same structure the
web preview renders — so the two cannot drift into disagreeing about
what the invoice says.

ReportLab rather than an HTML-to-PDF engine: WeasyPrint and its peers
need pango, cairo and gdk-pixbuf present in the image, and this is a
self-hosted product where every native library is a cost paid by the
person running the server. ReportLab ships pure-Python wheels.

**The page has three bands, and that is the whole layout.**

    ┌──────────────────────────────┐
    │ header: title, number, rule  │
    │ parties, dates               │
    ├──────────────────────────────┤
    │ body: line items, totals     │  ← grows, splits across pages
    │                              │
    │            (whitespace)      │
    ├──────────────────────────────┤
    │ footer: how to pay, notes    │  ← pinned to the bottom margin
    │ footer note, page number     │
    └──────────────────────────────┘

The footer is measured before the body is drawn and pinned to the bottom
margin, because that is where a reader looks for it on a real invoice.
Letting it follow the totals put "how to pay me" halfway up an otherwise
empty page, which is the layout this replaces.

The body splits when it does not fit. A long invoice is genuinely more
than one page, and the alternative — what this module did before — is
drawing line items off the bottom edge where nobody ever sees them.
"""
import io
from decimal import Decimal
from typing import Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from app.services.invoice_document import DEFAULT_ACCENT, InvoiceDocument

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 18 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
#: Breathing room between the body and the footer band. Without it a long
#: invoice's totals sit flush against the payment details.
FOOTER_GAP = 12 * mm

INK = colors.HexColor("#18181b")
MUTED = colors.HexColor("#71717a")
RULE = colors.HexColor("#e4e4e7")


def _accent(value: Optional[str]) -> colors.Color:
    """The sender's colour, or the neutral ink when it is unusable.

    The value is user input reaching a PDF generator, so a malformed one
    must degrade rather than raise: an invoice that will not render is
    worse than an invoice in the wrong colour.
    """
    try:
        return colors.HexColor(value or DEFAULT_ACCENT)
    except Exception:
        return colors.HexColor(DEFAULT_ACCENT)


def _money(amount: Decimal, currency: str) -> str:
    """Amount with its currency code.

    Deliberately not locale-formatted: the server has no reliable locale
    for the *reader*, who may be in a different country from the issuer,
    and a misplaced thousands separator on an invoice is worse than a
    plain one. The code is always shown so the number is unambiguous.
    """
    quantised = Decimal(amount).quantize(Decimal("0.01"))
    return f"{currency} {quantised:,.2f}"


def _para(
    text: str,
    size: float = 9.5,
    color: colors.Color = INK,
    bold: bool = False,
    leading: Optional[float] = None,
    align: int = 0,
) -> Paragraph:
    style = ParagraphStyle(
        "cell",
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        leading=leading or size * 1.45,
        textColor=color,
        alignment=align,
    )
    # Escaped, because everything reaching this function is somebody's
    # data — a client called "Alpha & Beta <Ltda>", a line reading
    # "R&D <phase 1>". ReportLab reads its argument as markup, so
    # unescaped text is not merely a crash risk: `<Ltda>` is silently
    # swallowed as an unknown tag and the client's name goes out on the
    # document truncated, with nothing to notice it by.
    #
    # The line break is applied after escaping, since it is the one piece
    # of markup this function means.
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _title(document: InvoiceDocument) -> str:
    """What the page says it is. A statement must never read as the
    invoice: the invoice is the document as issued, and a second page
    with the same title and different figures is the drift the archive
    exists to prevent."""
    return document.labels["statement"] if document.statement else document.labels["invoice"]


def _label(text: str) -> Paragraph:
    """A section label. Small, spaced, quiet — the same treatment the web
    preview gives it, so the two read as one design."""
    return _para(text.upper(), size=7, color=MUTED, bold=True, leading=9)


def _stack(canvas, flowables: list, x: float, y: float, width: float) -> float:
    """Draw a stack of flowables downward from `y`, returning the new y."""
    for flowable in flowables:
        _, height = flowable.wrap(width, PAGE_HEIGHT)
        y -= height
        flowable.drawOn(canvas, x, y)
    return y


def _stack_height(flowables: list, width: float) -> float:
    return sum(flowable.wrap(width, PAGE_HEIGHT)[1] for flowable in flowables)


def _party_block(
    title: str,
    name: Optional[str],
    legal_name: Optional[str],
    address: Optional[str],
    email: Optional[str],
    tax_ids: list[tuple[str, str]],
) -> list:
    rows = [_label(title), _para("", size=3)]
    if name:
        rows.append(_para(name, size=10.5, bold=True))
    # Only when it differs: printing "Alpha ME / Alpha ME" reads as a bug.
    if legal_name and legal_name != name:
        rows.append(_para(legal_name, size=9, color=MUTED))
    for label, value in tax_ids:
        rows.append(_para(f"{label} {value}", size=9, color=MUTED))
    if address:
        rows.append(_para(address, size=9, color=MUTED))
    if email:
        rows.append(_para(email, size=9, color=MUTED))
    return rows


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------
def _draw_header(canvas, document: InvoiceDocument, accent, logo_bytes) -> float:
    """Title, number and the accent rule. Returns the y below it."""
    y = PAGE_HEIGHT - MARGIN

    if logo_bytes:
        try:
            from reportlab.lib.utils import ImageReader

            image = ImageReader(io.BytesIO(logo_bytes))
            iw, ih = image.getSize()
            height = 13 * mm
            width = min(height * (iw / ih) if ih else height, 45 * mm)
            canvas.drawImage(
                image, MARGIN, y - height, width=width, height=height,
                mask="auto", preserveAspectRatio=True, anchor="sw",
            )
            y -= height + 5 * mm
        except Exception:
            # A broken image must not cost the whole document.
            pass

    canvas.setFont("Helvetica-Bold", 19)
    canvas.setFillColor(INK)
    canvas.drawString(MARGIN, y - 6.5 * mm, _title(document))

    if document.number:
        canvas.setFont("Helvetica-Bold", 12.5)
        canvas.setFillColor(accent)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, y - 6.5 * mm, document.number)

    y -= 10 * mm
    canvas.setStrokeColor(accent)
    canvas.setLineWidth(1.6)
    canvas.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)
    return y - 9 * mm


def _draw_parties_and_dates(canvas, document: InvoiceDocument, y: float) -> float:
    column = (CONTENT_WIDTH - 12 * mm) / 2
    left = _stack(
        canvas,
        _party_block(
            document.labels["from"], document.issuer.name, document.issuer.legal_name,
            document.issuer.address, None, document.issuer.tax_ids,
        ),
        MARGIN, y, column,
    )
    right = _stack(
        canvas,
        _party_block(
            document.labels["billTo"], document.client.name, None,
            document.client.address, document.client.email, document.client.tax_ids,
        ),
        MARGIN + column + 12 * mm, y, column,
    )
    y = min(left, right) - 9 * mm

    meta: list[tuple[str, str]] = [
        (document.labels["issueDate"], document.issue_date.isoformat()),
        (document.labels["dueDate"], document.due_date.isoformat()),
        *document.custom_fields,
    ]
    table = Table(
        [
            [_label(label) for label, _ in meta],
            [_para(value, size=9.5) for _, value in meta],
        ],
        colWidths=[CONTENT_WIDTH / len(meta)] * len(meta),
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    _, height = table.wrap(CONTENT_WIDTH, PAGE_HEIGHT)
    y -= height
    table.drawOn(canvas, MARGIN, y)
    return y - 11 * mm


def _schedule_table(document: InvoiceDocument) -> Optional[Table]:
    """The schedule, when the money is expected on more than one date.

    Drawn under the dates and above the lines: it is about when, not
    what. A table rather than part of the header so that a long one
    breaks across pages the way the lines do, instead of running off
    the bottom of page one.
    """
    if not document.installments:
        return None
    # The same treatment as the lines below it: a ruled header, a hairline
    # between rows, and every money column right-aligned, header included.
    header = [
        _label(document.labels["schedule"]),
        _label(document.labels["dueDate"]),
        _para(document.labels["amount"].upper(), size=7, color=MUTED, bold=True, align=TA_RIGHT),
    ]
    rows = [header]
    for index, installment in enumerate(document.installments, start=1):
        rows.append([
            _para(installment.label or f"{index}/{len(document.installments)}"),
            _para(installment.due_date.isoformat(), color=MUTED),
            _para(_money(installment.amount, document.currency), align=TA_RIGHT),
        ])
    table = Table(
        rows,
        colWidths=[CONTENT_WIDTH * 0.5, CONTENT_WIDTH * 0.25, CONTENT_WIDTH * 0.25],
        repeatRows=1,
        splitInRow=1,
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _movements_table(document: InvoiceDocument) -> Optional[Table]:
    """On a statement: every payment and deduction, by date, so the reader
    can walk from the total to the balance line by line."""
    if not document.movements:
        return None
    header = [
        _label(document.labels["history"]),
        _label(document.labels["date"]),
        _para(document.labels["amount"].upper(), size=7, color=MUTED, bold=True, align=TA_RIGHT),
    ]
    rows = [header]
    for movement in document.movements:
        rows.append([
            _para(movement.description),
            _para(movement.day.isoformat(), color=MUTED),
            _para(_money(movement.amount, document.currency), align=TA_RIGHT),
        ])
    table = Table(
        rows,
        colWidths=[CONTENT_WIDTH * 0.5, CONTENT_WIDTH * 0.25, CONTENT_WIDTH * 0.25],
        repeatRows=1,
        splitInRow=1,
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _lines_table(document: InvoiceDocument) -> Optional[Table]:
    if not document.lines:
        return None
    header = [
        _label(document.labels["description"]),
        _para(document.labels["quantity"].upper(), size=7, color=MUTED, bold=True, align=TA_RIGHT),
        _para(document.labels["unitPrice"].upper(), size=7, color=MUTED, bold=True, align=TA_RIGHT),
        _para(document.labels["amount"].upper(), size=7, color=MUTED, bold=True, align=TA_RIGHT),
    ]
    rows = [header]
    for line in document.lines:
        quantity = Decimal(line.quantity).normalize()
        # "12 hours", not "12": the unit is what lets the person paying
        # check the arithmetic against what they agreed to.
        counted = f"{quantity:f} {line.unit}" if line.unit else f"{quantity:f}"
        rows.append([
            _para(line.description),
            _para(counted, align=TA_RIGHT),
            _para(_money(line.unit_price, document.currency), align=TA_RIGHT),
            _para(_money(line.total, document.currency), align=TA_RIGHT),
        ])
    widths = [
        CONTENT_WIDTH * 0.46, CONTENT_WIDTH * 0.12,
        CONTENT_WIDTH * 0.21, CONTENT_WIDTH * 0.21,
    ]
    # `splitInRow` lets a single row break across pages. Without it a
    # description taller than one page cannot be split at all, and the
    # paginating loop below asks for a fresh page, fails again on the
    # empty page, and asks again — forever.
    table = Table(rows, colWidths=widths, repeatRows=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _totals_table(document: InvoiceDocument, accent) -> Table:
    rows: list[tuple[str, str, bool]] = []
    if document.lines:
        rows.append((document.labels["subtotal"], _money(document.subtotal, document.currency), False))
    if document.discount and document.discount > 0:
        rows.append((document.labels["discount"], f"-{_money(document.discount, document.currency)}", False))
    if document.tax_total and document.tax_total > 0:
        rows.append((document.labels["tax"], _money(document.tax_total, document.currency), False))
    rows.append((document.labels["total"], _money(document.total, document.currency), True))
    # Paid and balance only once something has settled: on an untouched
    # invoice they restate the total twice and add nothing. Deductions get
    # their own row, or the page would say total 3,000, paid 1,455,
    # balance 1,500, and the 45 the client withheld would be missing.
    paid = document.amount_paid or Decimal("0")
    deducted = document.amount_deducted or Decimal("0")
    # A statement always shows them: saying what is left is its purpose.
    if paid > 0 or deducted > 0 or document.statement:
        if paid > 0:
            rows.append((document.labels["paid"], _money(paid, document.currency), False))
        if deducted > 0:
            rows.append((document.labels["deducted"], _money(deducted, document.currency), False))
        rows.append((document.labels["balance"], _money(document.balance, document.currency), True))

    width = 74 * mm
    table = Table(
        [
            [
                _para(label, size=10 if strong else 9.5, bold=strong, color=INK if strong else MUTED),
                _para(
                    value, size=11.5 if strong else 9.5, bold=strong,
                    color=accent if strong else INK, align=TA_RIGHT,
                ),
            ]
            for label, value, strong in rows
        ],
        colWidths=[width * 0.5, width * 0.5],
    )
    style = [
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]
    for index, (_, _, strong) in enumerate(rows):
        if strong:
            style.append(("LINEABOVE", (0, index), (-1, index), 0.6, RULE))
    table.setStyle(TableStyle(style))
    return table


def _footer_flowables(document: InvoiceDocument) -> tuple[Optional[Table], list]:
    """The bottom band: how to pay, notes, and the closing line.

    Payment details and notes sit side by side because they are read
    together and neither deserves the full width. Returns them separately
    from the closing note so the caller can rule between the two.
    """
    columns = []
    for title, body in (
        (document.labels["paymentDetails"], document.payment_details),
        (document.labels["notes"], document.notes),
    ):
        columns.append(
            [_label(title), _para("", size=2), _para(body, size=9.5)] if body else []
        )

    table = None
    if any(columns):
        width = (CONTENT_WIDTH - 10 * mm) / 2
        table = Table(
            [[columns[0] or "", columns[1] or ""]],
            colWidths=[width, width],
        )
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("RIGHTPADDING", (-1, 0), (-1, 0), 0),
            ("LEFTPADDING", (-1, 0), (-1, 0), 10 * mm),
        ]))

    closing = [_para(document.footer_note, size=8.5, color=MUTED)] if document.footer_note else []
    return table, closing


def _footer_height(footer: Optional[Table], closing: list) -> float:
    """How much the bottom band needs, so the body knows where to stop."""
    height = 0.0
    if footer is not None:
        height += footer.wrap(CONTENT_WIDTH, PAGE_HEIGHT)[1]
    if closing:
        if footer is not None:
            height += 6 * mm  # the rule between the two
        height += _stack_height(closing, CONTENT_WIDTH)
    return height


def _draw_footer(canvas, footer: Optional[Table], closing: list) -> None:
    """Pinned to the bottom margin, which is where a reader looks."""
    y = MARGIN
    if closing:
        y = _stack(canvas, closing, MARGIN, MARGIN + _stack_height(closing, CONTENT_WIDTH), CONTENT_WIDTH)
        y = MARGIN + _stack_height(closing, CONTENT_WIDTH) + 6 * mm
        if footer is not None:
            canvas.setStrokeColor(RULE)
            canvas.setLineWidth(0.5)
            canvas.line(MARGIN, y - 3 * mm, PAGE_WIDTH - MARGIN, y - 3 * mm)
    if footer is not None:
        footer.wrap(CONTENT_WIDTH, PAGE_HEIGHT)
        footer.drawOn(canvas, MARGIN, y)


def _draw_page_number(canvas, page: int, total: int) -> None:
    """Only when there is more than one page. On a single-page invoice a
    "1 / 1" is noise that makes the document look generated."""
    if total <= 1:
        return
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(PAGE_WIDTH - MARGIN, MARGIN - 5 * mm, f"{page} / {total}")


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def render_pdf(document: InvoiceDocument, logo_bytes: Optional[bytes] = None) -> bytes:
    """The document as PDF bytes.

    `logo_bytes` is passed in rather than fetched here: this function does
    no I/O, which keeps it trivially testable and keeps a slow or hostile
    URL from being reachable from inside a renderer.
    """
    accent = _accent(document.accent_color)
    buffer = io.BytesIO()
    canvas = pdf_canvas.Canvas(buffer, pagesize=A4)
    canvas.setTitle(f"{_title(document)} {document.number or ''}".strip())

    footer, closing = _footer_flowables(document)
    footer_height = _footer_height(footer, closing)
    floor = MARGIN + footer_height + FOOTER_GAP

    lines = _lines_table(document)
    totals = _totals_table(document, accent)
    totals_height = totals.wrap(74 * mm, PAGE_HEIGHT)[1]

    # Page 1 header is the full masthead; continuations get a slim one, so
    # a reader who picks up page 3 still knows what they are holding.
    pages: list[list] = []
    y = _draw_parties_and_dates(canvas, document, _draw_header(canvas, document, accent, logo_bytes))

    # The totals must share the last page with the last table, so its
    # final chunk needs room for both.
    reserve = totals_height + 8 * mm

    # When, what, then (on a statement) what settled it. Each table is
    # flowed in turn; only the last one has to leave room for the totals.
    tables = [
        table
        for table in (_schedule_table(document), lines, _movements_table(document))
        if table is not None
    ]
    for index, table in enumerate(tables):
        last = index == len(tables) - 1
        y = _flow(canvas, document, accent, table, y, floor, pages, reserve=reserve if last else 0.0)
        if not last:
            y -= 8 * mm

    y -= 8 * mm
    if y - totals_height < floor:
        # Whatever the tables did, the totals never sink into the footer.
        pages.append([])
        canvas.showPage()
        y = _draw_continuation_header(canvas, document, accent)
    totals.drawOn(canvas, PAGE_WIDTH - MARGIN - 74 * mm, y - totals_height)

    _draw_footer(canvas, footer, closing)
    total_pages = len(pages) + 1
    _draw_page_number(canvas, total_pages, total_pages)
    canvas.showPage()

    canvas.save()
    return buffer.getvalue()


def _flow(
    canvas,
    document: InvoiceDocument,
    accent,
    table: Table,
    y: float,
    floor: float,
    pages: list[list],
    reserve: float = 0.0,
) -> float:
    """Draw `table` downwards from `y`, breaking onto continuation pages
    as needed, and return the y just under it.

    `reserve` is room the last chunk must leave under itself on its page,
    for whatever has to share that page with it. `pages` grows by one per
    page break, for the page count.
    """
    remaining: Optional[Table] = table
    # Whether the next attempt is happening on an otherwise empty page,
    # which is what tells a "too full" failure apart from a "will never
    # fit" one.
    on_fresh_page = False
    while remaining is not None:
        available = y - floor
        needed = remaining.wrap(CONTENT_WIDTH, PAGE_HEIGHT)[1]
        if needed + reserve <= available:
            remaining.drawOn(canvas, MARGIN, y - needed)
            return y - needed

        parts = remaining.split(CONTENT_WIDTH, available)
        if len(parts) == 1 and reserve:
            # The whole of it fits this page, but not with what has to
            # follow it. Split so the tail and what follows share the
            # next page, instead of drawing it whole over the reserve.
            parts = remaining.split(CONTENT_WIDTH, available - reserve)
        if len(parts) < 2:
            # Cannot split into this space. Normally that means the page
            # is too full, and a fresh one solves it.
            #
            # If we are *already* at the top of a fresh page, it does
            # not: the content will not fit anywhere, and asking for
            # another page would ask forever. Draw it and move on; an
            # overrun on one invoice beats a request that never returns
            # and holds a worker until it is killed.
            if on_fresh_page:
                remaining.drawOn(canvas, MARGIN, y - needed)
                return y - needed
            pages.append([])
            canvas.showPage()
            y = _draw_continuation_header(canvas, document, accent)
            on_fresh_page = True
            continue

        head, tail = parts[0], parts[1]
        head_height = head.wrap(CONTENT_WIDTH, PAGE_HEIGHT)[1]
        head.drawOn(canvas, MARGIN, y - head_height)
        pages.append([])
        canvas.showPage()
        y = _draw_continuation_header(canvas, document, accent)
        remaining = tail
        # The page the tail lands on carries only the continuation
        # header, so it counts as fresh for the same reason.
        on_fresh_page = True
    return y


def _draw_continuation_header(canvas, document: InvoiceDocument, accent) -> float:
    """A slim masthead for pages 2+.

    Enough to identify the document without repeating the whole party
    block, which on a continuation page is filler.
    """
    y = PAGE_HEIGHT - MARGIN
    canvas.setFont("Helvetica-Bold", 11)
    canvas.setFillColor(INK)
    canvas.drawString(MARGIN, y - 4 * mm, _title(document))
    if document.number:
        canvas.setFont("Helvetica-Bold", 11)
        canvas.setFillColor(accent)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, y - 4 * mm, document.number)
    y -= 7 * mm
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)
    return y - 7 * mm
