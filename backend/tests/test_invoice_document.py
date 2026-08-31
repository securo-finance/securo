"""The rendered document: resolution, PDF bytes, and the shared link.

Two properties matter most here and both are asserted directly:

  1. **The snapshot wins.** A document already in a client's hands must
     not change when settings do.
  2. **The screen and the PDF agree.** They consume one structure, and
     the tests read that structure rather than either renderer.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.fiscal.registry import TaxIdKind, apply_mask, format_for_display
from app.models.payee import Payee, PayeeTaxId
from app.services import invoice_pdf

TODAY = date.today()


@pytest_asyncio.fixture
async def business_ws(client: AsyncClient, auth_headers) -> dict:
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Consultoria", "kind": "business", "self_membership": True},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def biz_headers(auth_headers, business_ws) -> dict:
    return {**auth_headers, "X-Workspace-Id": business_ws["id"]}


@pytest_asyncio.fixture
async def client_payee(session: AsyncSession, business_ws, test_user) -> Payee:
    payee = Payee(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=uuid.UUID(business_ws["id"]),
        name="Beta Tecnologia LTDA", source="manual", address="Av Paulista, 1000",
        email="financeiro@beta.com",
    )
    session.add(payee)
    await session.flush()
    session.add(
        PayeeTaxId(
            payee_id=payee.id, workspace_id=uuid.UUID(business_ws["id"]),
            kind="cnpj", value="11222333000181",
        )
    )
    await session.commit()
    return payee


async def make_invoice(client, headers, **overrides):
    payload = {
        "total": "3000.00",
        "due_date": str(TODAY + timedelta(days=15)),
        "lines": [{"description": "Consultoria", "quantity": "10", "unit_price": "300.00"}],
    }
    payload.update(overrides)
    resp = await client.post("/api/invoices", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# The mask, shared by the PDF and the screen
# ---------------------------------------------------------------------------
class TestMask:
    def test_formats_a_full_document(self):
        assert format_for_display(TaxIdKind.CNPJ, "11222333000181") == "11.222.333/0001-81"
        assert format_for_display(TaxIdKind.CPF, "52998224725") == "529.982.247-25"

    def test_shows_a_short_value_as_stored(self):
        """A half-masked document number ("12.3") reads as corruption. If
        the value does not fill the mask, it is shown as it is."""
        assert apply_mask("123", "##.###.###/####-##") == "123"

    def test_shows_a_long_value_as_stored(self):
        assert apply_mask("1122233300018199", "##.###.###/####-##") == "1122233300018199"

    def test_a_kind_with_no_mask_is_untouched(self):
        # Which is what keeps a jurisdiction nobody has described from
        # being mangled.
        assert apply_mask("DE811907980", None) == "DE811907980"


# ---------------------------------------------------------------------------
# Document resolution
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestDocument:
    async def test_carries_both_parties_and_the_lines(
        self, client: AsyncClient, biz_headers, client_payee
    ):
        await client.patch(
            "/api/invoices/issuer",
            headers=biz_headers,
            json={
                "legal_name": "Alpha Consultoria ME",
                "address": "Rua das Flores, 10",
                "tax_ids": [{"kind": "cnpj", "value": "11.222.333/0001-81"}],
            },
        )
        invoice = await make_invoice(client, biz_headers, payee_id=str(client_payee.id))
        resp = await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
        assert resp.status_code == 200, resp.text
        doc = resp.json()

        assert doc["issuer"]["legal_name"] == "Alpha Consultoria ME"
        # Masked server-side: the PDF has no frontend to ask.
        assert doc["issuer"]["tax_ids"][0]["value"] == "11.222.333/0001-81"
        assert doc["client"]["name"] == "Beta Tecnologia LTDA"
        assert doc["client"]["tax_ids"][0]["value"] == "11.222.333/0001-81"
        assert doc["lines"][0]["description"] == "Consultoria"
        assert doc["total"] == "3000.00"
        assert doc["has_line_items"] is True

    async def test_an_invoice_with_no_lines_says_so(self, client: AsyncClient, biz_headers):
        """The majority Brazilian case: the NF was issued at the
        prefeitura and this is only tracking the money. It must not
        pretend to be a document."""
        invoice = await make_invoice(client, biz_headers, lines=[])
        resp = await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
        assert resp.json()["has_line_items"] is False
        assert resp.json()["lines"] == []

    async def test_labels_default_and_can_be_overridden(
        self, client: AsyncClient, biz_headers
    ):
        # The conftest user reads pt-BR, so the workspace does too and the
        # document defaults to the Portuguese pack. Asserted against the
        # pack rather than a hardcoded word: the point is that the issuer's
        # language decides, not which language that happens to be.
        from app.services.invoice_document import default_labels

        default = await make_invoice(client, biz_headers)
        doc = (
            await client.get(f"/api/invoices/{default['id']}/document", headers=biz_headers)
        ).json()
        assert doc["labels"]["quantity"] == default_labels("pt-BR")["quantity"]

        await client.patch(
            "/api/invoices/settings",
            headers=biz_headers,
            json={"template": {"labels": {"quantity": "Horas", "invoice": "Fatura"}}},
        )
        customised = await make_invoice(client, biz_headers)
        doc = (
            await client.get(f"/api/invoices/{customised['id']}/document", headers=biz_headers)
        ).json()
        assert doc["labels"]["quantity"] == "Horas"
        assert doc["labels"]["invoice"] == "Fatura"
        # Untouched labels keep their default rather than disappearing.
        assert doc["labels"]["total"] == default_labels("pt-BR")["total"]

    async def test_an_unknown_label_key_is_ignored(self, client: AsyncClient, biz_headers):
        """The template is free-form jsonb, so it can hold anything a hand
        edit put there. Unknown keys must not become UI."""
        await client.patch(
            "/api/invoices/settings",
            headers=biz_headers,
            json={"template": {"labels": {"nonsense": "x", "quantity": "Horas"}}},
        )
        invoice = await make_invoice(client, biz_headers)
        doc = (
            await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
        ).json()
        assert "nonsense" not in doc["labels"]
        assert doc["labels"]["quantity"] == "Horas"

    async def test_the_snapshot_beats_later_settings(
        self, client: AsyncClient, biz_headers, client_payee
    ):
        """The rule the whole design turns on: a document in a client's
        hands does not change when the sender edits their profile."""
        await client.patch(
            "/api/invoices/settings",
            headers=biz_headers,
            json={"issuer_display_name": "Alpha ME", "accent_color": "#4f46e5",
                  "payment_details": "Pix: alpha@exemplo.com"},
        )
        invoice = await make_invoice(client, biz_headers, payee_id=str(client_payee.id))

        await client.patch(
            "/api/invoices/settings",
            headers=biz_headers,
            json={"issuer_display_name": "Gamma LTDA", "accent_color": "#dc2626",
                  "payment_details": "Pix: outro@exemplo.com"},
        )
        doc = (
            await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
        ).json()
        assert doc["issuer"]["name"] == "Alpha ME"
        assert doc["accent_color"] == "#4f46e5"
        assert doc["payment_details"] == "Pix: alpha@exemplo.com"

    async def test_renaming_a_client_does_not_rewrite_their_document(
        self, client: AsyncClient, biz_headers, client_payee, session: AsyncSession
    ):
        invoice = await make_invoice(client, biz_headers, payee_id=str(client_payee.id))
        await client.patch(
            f"/api/payees/{client_payee.id}", headers=biz_headers, json={"name": "Beta S.A."}
        )
        doc = (
            await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
        ).json()
        assert doc["client"]["name"] == "Beta Tecnologia LTDA"

    async def test_custom_fields_follow_the_definitions(
        self, client: AsyncClient, biz_headers
    ):
        await client.patch(
            "/api/invoices/settings",
            headers=biz_headers,
            json={"template": {"custom_fields": [{"key": "po", "label": "PO number"}]}},
        )
        invoice = await make_invoice(
            client, biz_headers, custom_fields={"po": "PO-4471", "stray": "ignored"}
        )
        doc = (
            await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
        ).json()
        # Driven by the definitions, so a value with no definition never
        # reaches the page.
        assert doc["custom_fields"] == [{"label": "PO number", "value": "PO-4471"}]


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestPdf:
    async def test_downloads_a_real_pdf(self, client: AsyncClient, biz_headers, client_payee):
        invoice = await make_invoice(client, biz_headers, payee_id=str(client_payee.id))
        resp = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF-")
        assert "attachment" in resp.headers["content-disposition"]

    async def test_the_pdf_carries_what_the_document_says(
        self, client: AsyncClient, biz_headers, client_payee
    ):
        """Read back through pypdf rather than trusting the byte count:
        a PDF that renders blank is still a valid PDF."""
        import pypdf

        await client.patch(
            "/api/invoices/issuer",
            headers=biz_headers,
            json={"legal_name": "Alpha Consultoria ME",
                  "tax_ids": [{"kind": "cnpj", "value": "11222333000181"}]},
        )
        invoice = await make_invoice(client, biz_headers, payee_id=str(client_payee.id))
        resp = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)

        import io

        text = pypdf.PdfReader(io.BytesIO(resp.content)).pages[0].extract_text()
        assert "Beta Tecnologia LTDA" in text
        assert "Alpha Consultoria ME" in text
        assert "11.222.333/0001-81" in text
        assert "Consultoria" in text
        assert "3,000.00" in text
        assert invoice["due_date"] in text

    async def test_renders_without_a_client_or_any_profile(
        self, client: AsyncClient, biz_headers
    ):
        """A workspace that has filled nothing in still gets a usable
        page — every field on the document is optional by design."""
        invoice = await make_invoice(client, biz_headers, lines=[])
        resp = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)
        assert resp.status_code == 200
        assert resp.content.startswith(b"%PDF-")

    def test_a_broken_accent_colour_degrades_instead_of_raising(self):
        """User input reaching a renderer must never cost the document."""
        from app.services.invoice_pdf import _accent

        assert _accent("not-a-colour") is not None
        assert _accent(None) is not None
        assert _accent("#4f46e5") is not None

    async def test_a_broken_logo_does_not_cost_the_document(
        self, client: AsyncClient, biz_headers
    ):
        invoice = await make_invoice(client, biz_headers)
        settings = (await client.get("/api/invoices/settings", headers=biz_headers)).json()
        doc_resp = await client.get(
            f"/api/invoices/{invoice['id']}/document", headers=biz_headers
        )
        assert doc_resp.status_code == 200
        # Feed the renderer bytes that are not an image at all.
        from app.services.invoice_document import (
            DEFAULT_LABELS, DocumentParty, InvoiceDocument,
        )

        document = InvoiceDocument(
            number="1", status="open", state="open", issue_date=TODAY, due_date=TODAY,
            currency="USD", subtotal=Decimal("0"), discount=Decimal("0"),
            tax_total=Decimal("0"), total=Decimal("10"), amount_paid=Decimal("0"),
            balance=Decimal("10"), issuer=DocumentParty(name="A"),
            client=DocumentParty(name="B"), lines=[], labels=dict(DEFAULT_LABELS),
            accent_color="#000000", logo_id=None, payment_details=None, notes=None,
            footer_note=None, custom_fields=[], has_line_items=False,
        )
        pdf = invoice_pdf.render_pdf(document, logo_bytes=b"not an image")
        assert pdf.startswith(b"%PDF-")
        assert settings is not None


# ---------------------------------------------------------------------------
# Sharing — the only unauthenticated surface
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestSharing:
    async def test_a_link_serves_the_document_without_auth(
        self, client: AsyncClient, biz_headers, client_payee
    ):
        invoice = await make_invoice(client, biz_headers, payee_id=str(client_payee.id))
        created = await client.post(f"/api/invoices/{invoice['id']}/share", headers=biz_headers)
        assert created.status_code == 201, created.text
        token = created.json()["token"]
        assert created.json()["path"] == f"/i/{token}"

        # No Authorization header at all.
        public = await client.get(f"/api/public/invoices/{token}")
        assert public.status_code == 200, public.text
        assert public.json()["client"]["name"] == "Beta Tecnologia LTDA"

    async def test_the_public_view_leaks_nothing_internal(
        self, client: AsyncClient, biz_headers, client_payee
    ):
        invoice = await make_invoice(
            client, biz_headers, payee_id=str(client_payee.id),
            internal_notes="cliente reclamou do prazo",
        )
        token = (
            await client.post(f"/api/invoices/{invoice['id']}/share", headers=biz_headers)
        ).json()["token"]
        body = (await client.get(f"/api/public/invoices/{token}")).text
        assert "cliente reclamou" not in body
        assert "internal_notes" not in body
        assert "allocations" not in body
        assert "payee_id" not in body

    async def test_the_token_is_not_derived_from_the_id(
        self, client: AsyncClient, biz_headers
    ):
        invoice = await make_invoice(client, biz_headers)
        token = (
            await client.post(f"/api/invoices/{invoice['id']}/share", headers=biz_headers)
        ).json()["token"]
        assert invoice["id"].replace("-", "") not in token
        assert len(token) >= 32

    async def test_asking_twice_returns_the_same_link(
        self, client: AsyncClient, biz_headers
    ):
        """Otherwise every visit to the invoice page would silently
        invalidate the link the client was already sent."""
        invoice = await make_invoice(client, biz_headers)
        first = await client.post(f"/api/invoices/{invoice['id']}/share", headers=biz_headers)
        second = await client.post(f"/api/invoices/{invoice['id']}/share", headers=biz_headers)
        assert first.json()["token"] == second.json()["token"]

    async def test_revoking_makes_the_link_a_404(self, client: AsyncClient, biz_headers):
        invoice = await make_invoice(client, biz_headers)
        token = (
            await client.post(f"/api/invoices/{invoice['id']}/share", headers=biz_headers)
        ).json()["token"]
        assert (await client.get(f"/api/public/invoices/{token}")).status_code == 200

        revoked = await client.delete(f"/api/invoices/{invoice['id']}/share", headers=biz_headers)
        assert revoked.status_code == 204
        # Indistinguishable from a link that never existed.
        assert (await client.get(f"/api/public/invoices/{token}")).status_code == 404

    async def test_voiding_takes_the_link_down(self, client: AsyncClient, biz_headers):
        """A cancelled document must stop being served: a link that keeps
        working says the invoice still stands."""
        invoice = await make_invoice(client, biz_headers)
        token = (
            await client.post(f"/api/invoices/{invoice['id']}/share", headers=biz_headers)
        ).json()["token"]
        await client.post(f"/api/invoices/{invoice['id']}/void", headers=biz_headers)
        assert (await client.get(f"/api/public/invoices/{token}")).status_code == 404

    async def test_a_draft_cannot_be_shared(self, client: AsyncClient, biz_headers):
        await client.patch(
            "/api/invoices/settings", headers=biz_headers, json={"preset": "document"}
        )
        draft = await make_invoice(client, biz_headers)
        assert draft["status"] == "draft"
        resp = await client.post(f"/api/invoices/{draft['id']}/share", headers=biz_headers)
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "draft_not_shareable"

    async def test_an_unknown_token_is_a_404(self, client: AsyncClient):
        assert (await client.get("/api/public/invoices/nope")).status_code == 404

    async def test_the_public_pdf_opens_inline(self, client: AsyncClient, biz_headers):
        invoice = await make_invoice(client, biz_headers)
        token = (
            await client.post(f"/api/invoices/{invoice['id']}/share", headers=biz_headers)
        ).json()["token"]
        resp = await client.get(f"/api/public/invoices/{token}/pdf")
        assert resp.status_code == 200
        assert resp.content.startswith(b"%PDF-")
        # Someone opening a link expects to see the document, not to
        # receive a download.
        assert "inline" in resp.headers["content-disposition"]


# ---------------------------------------------------------------------------
# Issuer identity (T10)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
class TestIssuerProfile:
    async def test_stores_and_normalises_a_document(self, client: AsyncClient, biz_headers):
        resp = await client.patch(
            "/api/invoices/issuer",
            headers=biz_headers,
            json={"legal_name": "Alpha ME",
                  "tax_ids": [{"kind": "cnpj", "value": "11.222.333/0001-81"}]},
        )
        assert resp.status_code == 200, resp.text
        # Stored normalised, exactly as the payee side stores it.
        assert resp.json()["tax_ids"][0]["value"] == "11222333000181"

    async def test_refuses_an_invalid_document_with_the_same_validator(
        self, client: AsyncClient, biz_headers
    ):
        """One implementation, asserted from both call sites."""
        resp = await client.patch(
            "/api/invoices/issuer",
            headers=biz_headers,
            json={"tax_ids": [{"kind": "cnpj", "value": "11111111111111"}]},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"].startswith("invalid_tax_id:cnpj")

    async def test_sending_a_shorter_list_removes_a_document(
        self, client: AsyncClient, biz_headers
    ):
        await client.patch(
            "/api/invoices/issuer",
            headers=biz_headers,
            json={"tax_ids": [{"kind": "cnpj", "value": "11222333000181"},
                              {"kind": "cpf", "value": "529.982.247-25"}]},
        )
        resp = await client.patch(
            "/api/invoices/issuer",
            headers=biz_headers,
            json={"tax_ids": [{"kind": "cnpj", "value": "11222333000181"}]},
        )
        assert [t["kind"] for t in resp.json()["tax_ids"]] == ["cnpj"]

    async def test_omitting_tax_ids_leaves_them_untouched(
        self, client: AsyncClient, biz_headers
    ):
        await client.patch(
            "/api/invoices/issuer",
            headers=biz_headers,
            json={"tax_ids": [{"kind": "cnpj", "value": "11222333000181"}]},
        )
        resp = await client.patch(
            "/api/invoices/issuer", headers=biz_headers, json={"legal_name": "Alpha ME"}
        )
        assert len(resp.json()["tax_ids"]) == 1

    async def test_a_workspace_with_no_identity_still_works(
        self, client: AsyncClient, biz_headers
    ):
        """Nothing here is required until a document is rendered."""
        resp = await client.get("/api/invoices/issuer", headers=biz_headers)
        assert resp.status_code == 200
        assert resp.json()["legal_name"] is None
        assert resp.json()["tax_ids"] == []

    async def test_a_personal_workspace_cannot_reach_the_issuer_routes(
        self, client: AsyncClient, auth_headers, session: AsyncSession, test_user
    ):
        from sqlalchemy import select

        from app.models.workspace import Workspace, WorkspaceMember

        result = await session.execute(
            select(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == test_user.id, Workspace.kind == "personal")
            .limit(1)
        )
        personal = result.scalar_one()
        headers = {**auth_headers, "X-Workspace-Id": str(personal.id)}
        assert (await client.get("/api/invoices/issuer", headers=headers)).status_code == 404
        assert (
            await client.patch("/api/invoices/issuer", headers=headers, json={"legal_name": "x"})
        ).status_code == 404


# ---------------------------------------------------------------------------
# Page structure — the band layout and its pagination
# ---------------------------------------------------------------------------
def _doc(n_lines: int, footer_note: str = "Alpha ME"):
    """A document with `n_lines` items, for exercising page breaks.

    Built field by field rather than from a dict: keyword-splatting a
    heterogeneous mapping loses every type on the way in, and this is the
    one fixture the PDF tests all lean on.
    """
    from app.services.invoice_document import (
        DEFAULT_LABELS, DocumentLine, DocumentParty, InvoiceDocument,
    )

    return InvoiceDocument(
        number="FAT-7",
        status="open",
        state="open",
        issue_date=date(2026, 8, 26),
        due_date=date(2026, 9, 20),
        currency="BRL",
        subtotal=Decimal("1200"),
        discount=Decimal("0"),
        tax_total=Decimal("0"),
        total=Decimal("1200"),
        amount_paid=Decimal("0"),
        balance=Decimal("1200"),
        issuer=DocumentParty(name="Alpha ME", tax_ids=[]),
        client=DocumentParty(name="Beta LTDA", address=None, tax_ids=[]),
        lines=[
            DocumentLine(f"Item {i + 1}", Decimal("1"), Decimal("100"), Decimal("100"))
            for i in range(n_lines)
        ],
        labels=dict(DEFAULT_LABELS),
        accent_color="#4f46e5",
        logo_id=None,
        payment_details="Pix: alpha@exemplo.com",
        notes="Obrigado.",
        footer_note=footer_note,
        custom_fields=[],
        has_line_items=n_lines > 0,
    )


def _pages(document):
    import io

    import pypdf

    return pypdf.PdfReader(io.BytesIO(invoice_pdf.render_pdf(document))).pages


class TestPageStructure:
    def test_a_short_invoice_is_one_page(self):
        assert len(_pages(_doc(3))) == 1

    def test_a_long_invoice_paginates_instead_of_overflowing(self):
        """The regression this exists for.

        The renderer used to draw one page and let anything taller run off
        the bottom edge, where the line items were still in the file and
        invisible to every human who opened it.
        """
        pages = _pages(_doc(45))
        assert len(pages) > 1

    def test_no_line_item_is_lost_across_the_break(self):
        n = 45
        pages = _pages(_doc(n))
        text = "\n".join(p.extract_text() for p in pages)
        for i in range(n):
            assert f"Item {i + 1}" in text, f"line {i + 1} vanished"

    def test_every_page_identifies_the_document(self):
        """Someone holding page 3 has to know what it belongs to."""
        for page in _pages(_doc(45)):
            text = page.extract_text()
            assert "FAT-7" in text

    def test_the_payment_band_lands_on_the_last_page_only(self):
        pages = _pages(_doc(45))
        texts = [p.extract_text() for p in pages]
        assert "Pix: alpha@exemplo.com" in texts[-1]
        for text in texts[:-1]:
            assert "Pix: alpha@exemplo.com" not in text

    def test_totals_land_on_the_last_page_with_the_final_items(self):
        texts = [p.extract_text() for p in _pages(_doc(45))]
        assert "Item 45" in texts[-1]
        assert "1,200.00" in texts[-1]

    def test_page_numbers_appear_only_when_there_is_more_than_one(self):
        """"1 / 1" on a single-page invoice is noise that makes the
        document look machine-made."""
        single = _pages(_doc(3))[0].extract_text()
        assert "/ 1" not in single

        multi = _pages(_doc(45))
        assert f"/ {len(multi)}" in multi[-1].extract_text()

    def test_the_payment_band_sits_below_the_totals_on_the_page(self):
        """Position, not just presence: "how to pay me" halfway up an
        otherwise empty page is the layout this replaced."""
        import io

        import pypdf

        positions: dict[str, float] = {}

        def visitor(text, _cm, tm, *_args):
            stripped = text.strip()
            for needle in ("Total", "PAYMENT DETAILS"):
                if stripped.startswith(needle) and needle not in positions:
                    positions[needle] = tm[5]

        page = pypdf.PdfReader(io.BytesIO(invoice_pdf.render_pdf(_doc(3)))).pages[0]
        page.extract_text(visitor_text=visitor)

        assert "Total" in positions and "PAYMENT DETAILS" in positions
        # PDF y grows upward, so lower on the page is a smaller number.
        assert positions["PAYMENT DETAILS"] < positions["Total"]

    def test_an_invoice_with_no_lines_still_renders_the_bands(self):
        page = _pages(_doc(0))[0].extract_text()
        assert "Beta LTDA" in page
        assert "Pix: alpha@exemplo.com" in page


# ---------------------------------------------------------------------------
# Default labels follow the issuer, not the reader
# ---------------------------------------------------------------------------
class TestLabelPacks:
    def test_a_shipped_language_gets_its_own_words(self):
        from app.services.invoice_document import default_labels

        assert default_labels("pt-BR")["invoice"] == "Fatura"
        assert default_labels("de")["dueDate"] == "Fällig am"
        assert default_labels("fr")["invoice"] == "Facture"

    def test_regional_variants_share_a_pack(self):
        """`pt-BR` and `pt-PT` differ in ways a translator cares about and
        not in the eighteen words on an invoice."""
        from app.services.invoice_document import default_labels

        assert default_labels("pt-PT") == default_labels("pt-BR")

    def test_an_unshipped_language_falls_back_to_english(self):
        from app.services.invoice_document import DEFAULT_LABELS, default_labels

        assert default_labels("ja") == DEFAULT_LABELS
        assert default_labels(None) == DEFAULT_LABELS

    def test_every_pack_covers_every_label(self):
        """A half-translated document reads worse than an English one."""
        from app.services.invoice_document import DEFAULT_LABELS, LABEL_PACKS

        for language, pack in LABEL_PACKS.items():
            assert set(pack) == set(DEFAULT_LABELS), language


@pytest.mark.asyncio
class TestIssuerLanguage:
    async def test_a_brazilian_workspace_gets_a_portuguese_document(
        self, client: AsyncClient, auth_headers
    ):
        created = await client.post(
            "/api/workspaces",
            headers=auth_headers,
            json={"name": "Consultoria BR", "kind": "business",
                  "self_membership": True, "locale": "pt-BR"},
        )
        assert created.status_code == 201, created.text
        headers = {**auth_headers, "X-Workspace-Id": created.json()["id"]}

        invoice = await make_invoice(client, headers)
        doc = (
            await client.get(f"/api/invoices/{invoice['id']}/document", headers=headers)
        ).json()
        assert doc["labels"]["invoice"] == "Fatura"
        assert doc["labels"]["billTo"] == "Cliente"

    async def test_an_explicit_label_still_wins_over_the_pack(
        self, client: AsyncClient, auth_headers
    ):
        created = await client.post(
            "/api/workspaces",
            headers=auth_headers,
            json={"name": "Consultoria BR2", "kind": "business",
                  "self_membership": True, "locale": "pt-BR"},
        )
        headers = {**auth_headers, "X-Workspace-Id": created.json()["id"]}
        await client.patch(
            "/api/invoices/settings",
            headers=headers,
            json={"template": {"labels": {"invoice": "Recibo"}}},
        )
        invoice = await make_invoice(client, headers)
        doc = (
            await client.get(f"/api/invoices/{invoice['id']}/document", headers=headers)
        ).json()
        assert doc["labels"]["invoice"] == "Recibo"
        # Untouched labels still come from the pack, not from English.
        assert doc["labels"]["dueDate"] == "Vencimento"

    async def test_switching_the_interface_never_retitles_an_issued_document(
        self, client: AsyncClient, auth_headers
    ):
        """The words were the sender's choice at issuance. Changing the
        workspace language afterwards must not rewrite a document the
        client already holds."""
        created = await client.post(
            "/api/workspaces",
            headers=auth_headers,
            json={"name": "Consultoria BR3", "kind": "business",
                  "self_membership": True, "locale": "pt-BR"},
        )
        workspace_id = created.json()["id"]
        headers = {**auth_headers, "X-Workspace-Id": workspace_id}
        invoice = await make_invoice(client, headers)

        await client.patch(
            f"/api/workspaces/{workspace_id}", headers=headers, json={"locale": "en"}
        )
        doc = (
            await client.get(f"/api/invoices/{invoice['id']}/document", headers=headers)
        ).json()
        assert doc["labels"]["invoice"] == "Fatura"


@pytest.mark.asyncio
async def test_an_imported_number_is_rendered_with_the_source_prefix(
    client: AsyncClient, biz_headers
):
    """Our prefix belongs on our paper. A supplier reference of FAT-9931
    must not come back as INV-9931 because that is what we call ours."""
    await client.patch(
        "/api/invoices/settings", headers=biz_headers, json={"number_prefix": "INV-"}
    )
    imported = await make_invoice(
        client, biz_headers, origin="imported",
        external_source="erp", external_id="FAT-9931", external_number="FAT-9931",
    )
    doc = await client.get(f"/api/invoices/{imported['id']}/document", headers=biz_headers)
    assert doc.json()["number"] == "FAT-9931"

    bare = await make_invoice(
        client, biz_headers, origin="imported",
        external_source="erp", external_id="7",
    )
    doc = await client.get(f"/api/invoices/{bare['id']}/document", headers=biz_headers)
    assert doc.json()["number"] is None

    ours = await make_invoice(client, biz_headers)
    doc = await client.get(f"/api/invoices/{ours['id']}/document", headers=biz_headers)
    assert doc.json()["number"] == "INV-1"


@pytest.mark.asyncio
async def test_our_identity_stays_off_a_supplier_document(client: AsyncClient, biz_headers):
    """Our bank details under "pay to" on a bill we owe state the opposite
    of what is true, and our logo brands a page we did not write."""
    await client.patch(
        "/api/invoices/settings",
        headers=biz_headers,
        json={
            "payment_details": "Banco 001 · Ag 1234 · CC 56789-0",
            "footer_note": "Obrigado pela preferencia!",
            "accent_color": "#FF0000",
        },
    )
    # The logo arrives as a file now, on its own route.
    logo_id = (await _put_logo(client, biz_headers)).json()["logo_id"]

    bill = await make_invoice(
        client, biz_headers, direction="payable", origin="imported",
        external_source="erp", external_id="FAT-1",
    )
    doc = (
        await client.get(f"/api/invoices/{bill['id']}/document", headers=biz_headers)
    ).json()
    assert doc["logo_id"] is None
    assert doc["payment_details"] is None
    assert doc["footer_note"] is None
    assert doc["accent_color"] != "#FF0000"

    # On our own invoice every one of them is still printed.
    ours = await make_invoice(client, biz_headers)
    doc = (
        await client.get(f"/api/invoices/{ours['id']}/document", headers=biz_headers)
    ).json()
    assert doc["logo_id"] == logo_id
    assert doc["payment_details"] == "Banco 001 \u00b7 Ag 1234 \u00b7 CC 56789-0"
    assert doc["footer_note"] == "Obrigado pela preferencia!"
    assert doc["accent_color"] == "#FF0000"


# ---------------------------------------------------------------------------
# The logo is a file this workspace owns
# ---------------------------------------------------------------------------
def _png(width: int = 40, height: int = 20, colour: tuple = (79, 70, 229)) -> bytes:
    import io as _io

    from PIL import Image as _Image

    buf = _io.BytesIO()
    _Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


async def _put_logo(client, headers, data=None, content_type="image/png", name="logo.png"):
    return await client.post(
        "/api/invoices/settings/logo",
        headers=headers,
        files={"file": (name, data if data is not None else _png(), content_type)},
    )


@pytest.mark.asyncio
async def test_an_uploaded_logo_reaches_the_pdf(client: AsyncClient, biz_headers):
    """It never did: `render_pdf` took logo bytes and no caller passed
    any, so a workspace saw its mark on screen and got a file without."""
    resp = await _put_logo(client, biz_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["logo_id"]

    invoice = await make_invoice(client, biz_headers)
    pdf = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)
    import io as _io

    import pypdf as _pypdf

    reader = _pypdf.PdfReader(_io.BytesIO(pdf.content))
    assert len(list(reader.pages[0].images)) == 1


@pytest.mark.asyncio
async def test_replacing_the_logo_leaves_issued_documents_alone(
    client: AsyncClient, biz_headers
):
    """The freeze rule, applied to a file. An invoice a client is holding
    does not change its letterhead because the issuer rebranded."""
    first = (await _put_logo(client, biz_headers)).json()["logo_id"]
    invoice = await make_invoice(client, biz_headers)

    second = (await _put_logo(client, biz_headers, data=_png(colour=(200, 30, 30)))).json()[
        "logo_id"
    ]
    assert second != first

    doc = await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
    assert doc.json()["logo_url"] == f"/api/invoices/logo/{first}"

    # And the old file is still there to serve, because the document
    # still points at it.
    served = await client.get(f"/api/invoices/logo/{first}", headers=biz_headers)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"


@pytest.mark.asyncio
async def test_whatever_arrives_is_stored_as_a_bounded_png(
    client: AsyncClient, biz_headers
):
    """Re-encoding fixes the content type the route answers with, bounds
    the size every PDF carries, and drops the metadata a photo brings."""
    import io as _io

    from PIL import Image as _Image

    buf = _io.BytesIO()
    _Image.new("RGB", (3000, 1200), (10, 200, 90)).save(buf, format="JPEG")
    resp = await _put_logo(
        client, biz_headers, data=buf.getvalue(), content_type="image/jpeg", name="mark.jpg"
    )
    logo_id = resp.json()["logo_id"]

    served = await client.get(f"/api/invoices/logo/{logo_id}", headers=biz_headers)
    assert served.headers["content-type"] == "image/png"
    stored = _Image.open(_io.BytesIO(served.content))
    assert stored.format == "PNG"
    assert max(stored.size) <= 600


@pytest.mark.asyncio
async def test_a_file_that_is_not_an_image_is_refused(client: AsyncClient, biz_headers):
    resp = await _put_logo(
        client, biz_headers, data=b"%PDF-1.4 not an image", content_type="application/pdf",
        name="nope.pdf",
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_removing_the_logo_leaves_documents_that_used_it(
    client: AsyncClient, biz_headers
):
    logo_id = (await _put_logo(client, biz_headers)).json()["logo_id"]
    invoice = await make_invoice(client, biz_headers)

    cleared = await client.delete("/api/invoices/settings/logo", headers=biz_headers)
    assert cleared.json()["logo_id"] is None

    # The invoice issued under it still draws it.
    doc = await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
    assert doc.json()["logo_url"] == f"/api/invoices/logo/{logo_id}"


@pytest.mark.asyncio
async def test_the_shared_page_serves_the_logo_through_its_token(
    client: AsyncClient, biz_headers
):
    """The client opening a link has no session, so the mark has to be
    reachable by the token and by nothing else."""
    await _put_logo(client, biz_headers)
    invoice = await make_invoice(client, biz_headers)
    token = (
        await client.post(f"/api/invoices/{invoice['id']}/share", headers=biz_headers)
    ).json()["token"]

    shared = await client.get(f"/api/public/invoices/{token}")
    assert shared.json()["logo_url"] == f"/api/public/invoices/{token}/logo"
    # The workspace's internal handle for the file is not in the response.
    assert "logo_id" not in shared.json()

    served = await client.get(f"/api/public/invoices/{token}/logo")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"


@pytest.mark.asyncio
async def test_a_unit_is_printed_beside_the_quantity(client: AsyncClient, biz_headers):
    """"32 hours x 180.00" is a claim the payer can check. "32 x 180.00"
    is one they have to take on trust."""
    invoice = await make_invoice(
        client, biz_headers,
        lines=[{"description": "Desenvolvimento", "quantity": "32", "unit": "horas",
                "unit_price": "180.00"}],
        total="5760.00",
    )
    doc = (
        await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
    ).json()
    assert doc["lines"][0]["unit"] == "horas"

    import io as _io

    import pypdf as _pypdf

    pdf = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)
    text = _pypdf.PdfReader(_io.BytesIO(pdf.content)).pages[0].extract_text()
    assert "32 horas" in text


@pytest.mark.asyncio
async def test_a_line_without_a_unit_reads_as_it_always_did(
    client: AsyncClient, biz_headers
):
    """Optional means optional: nothing is invented for a line that
    counts nothing in particular."""
    invoice = await make_invoice(
        client, biz_headers,
        lines=[{"description": "Projeto fechado", "quantity": "1", "unit_price": "900.00"}],
        total="900.00",
    )
    doc = (
        await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
    ).json()
    assert doc["lines"][0]["unit"] is None


@pytest.mark.asyncio
async def test_a_client_name_with_markup_survives_the_pdf(
    client: AsyncClient, biz_headers, session: AsyncSession, business_ws, test_user
):
    """ReportLab reads its argument as markup, so an unescaped `<Ltda>`
    was swallowed as an unknown tag: the client's name went out on the
    document truncated, with nothing to notice it by."""
    payee = Payee(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=uuid.UUID(business_ws["id"]),
        name="Alpha & Beta <Ltda>",
    )
    session.add(payee)
    await session.commit()

    invoice = await make_invoice(
        client, biz_headers, payee_id=str(payee.id),
        lines=[{"description": "R&D <phase 1>", "quantity": "1", "unit_price": "100.00"}],
    )
    pdf = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)
    assert pdf.status_code == 200

    import io as _io

    import pypdf as _pypdf

    text = _pypdf.PdfReader(_io.BytesIO(pdf.content)).pages[0].extract_text()
    assert "Alpha & Beta <Ltda>" in text
    assert "R&D <phase 1>" in text


def test_an_oversized_raster_is_refused_before_it_is_decoded():
    """A 10,000 x 10,000 PNG of flat colour is a few hundred kilobytes and
    asks for hundreds of megabytes to decode. Every size limit above lets
    it through, and `thumbnail` only shrinks it long afterwards."""
    import io as _io

    from PIL import Image as _Image

    from app.services import invoice_logo_service

    buf = _io.BytesIO()
    _Image.new("RGB", (10000, 10000), (255, 0, 0)).save(buf, format="PNG")
    assert len(buf.getvalue()) < 1_000_000, "the point is that it is small"

    with pytest.raises(ValueError, match="megapixels"):
        invoice_logo_service.normalise(buf.getvalue(), "image/png")


def test_a_line_taller_than_a_page_still_finishes():
    """It used to hang, not fail. `split` returned nothing for a row
    taller than the space, the loop asked for a fresh page, failed again
    on the empty page and asked again — holding the worker until
    something killed it, which is worse than an error the caller can see.

    Guarded twice: the table may now break inside a row, and a failure to
    split on an already-empty page draws the content instead of asking
    for one more."""
    import signal

    huge = ("Servico de consultoria detalhado " * 400).strip()

    def give_up(signum, frame):
        raise AssertionError("pagination did not terminate")

    signal.signal(signal.SIGALRM, give_up)
    signal.alarm(20)
    try:
        document = _doc(1)
        document.lines[0].description = huge
        pdf = invoice_pdf.render_pdf(document)
    finally:
        signal.alarm(0)

    import io as _io

    import pypdf as _pypdf

    assert pdf.startswith(b"%PDF")
    assert len(_pypdf.PdfReader(_io.BytesIO(pdf)).pages) >= 1
