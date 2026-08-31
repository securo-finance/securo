"""The paper an invoice gathers.

The rule under every test here: an invoice is often a folder, not a
document. What it holds may have been written by somebody else, and when
it was, that file is the invoice — not a page we would draw from our own
fields.
"""
import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

TODAY = date.today()
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


@pytest_asyncio.fixture
async def personal_ws(session: AsyncSession, test_user):
    """The user's personal workspace, resolved by kind.

    Not the shared `test_workspace` fixture: that one takes the first row
    with no ORDER BY, so once these tests create a business workspace it
    can return either. Which workspace is which is the whole subject
    here, so it is pinned explicitly.
    """
    from sqlalchemy import select

    from app.models.workspace import Workspace, WorkspaceMember

    result = await session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == test_user.id, Workspace.kind == "personal")
        .order_by(Workspace.created_at.asc())
        .limit(1)
    )
    return result.scalar_one()


@pytest_asyncio.fixture
async def business_ws(client: AsyncClient, auth_headers) -> dict:
    resp = await client.post(
        "/api/workspaces",
        headers=auth_headers,
        json={"name": "Studio", "kind": "business", "self_membership": True},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def biz_headers(auth_headers, business_ws) -> dict:
    return {**auth_headers, "X-Workspace-Id": business_ws["id"]}


async def make_invoice(client, headers, **overrides) -> dict:
    payload = {
        "total": "1200.00",
        "due_date": str(TODAY + timedelta(days=20)),
        "lines": [{"description": "Servico", "quantity": "1", "unit_price": "1200.00"}],
    }
    payload.update(overrides)
    resp = await client.post("/api/invoices", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def upload(client, headers, invoice_id, *, filename="fatura.pdf", data=PDF, **fields):
    return await client.post(
        f"/api/invoices/{invoice_id}/attachments",
        headers=headers,
        files={"file": (filename, data, "application/pdf")},
        data={k: str(v) for k, v in fields.items()},
    )


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_invoice_gathers_several_documents(client: AsyncClient, biz_headers):
    """The bill, the fiscal document and the receipt arrive at different
    times from different places, and all belong to one debt."""
    invoice = await make_invoice(client, biz_headers)
    for kind, name in (("bill", "fatura.pdf"), ("fiscal", "nfe.pdf"), ("receipt", "comprovante.pdf")):
        resp = await upload(client, biz_headers, invoice["id"], filename=name, kind=kind)
        assert resp.status_code == 201, resp.text

    listing = await client.get(
        f"/api/invoices/{invoice['id']}/attachments", headers=biz_headers
    )
    assert [row["kind"] for row in listing.json()] == ["bill", "fiscal", "receipt"]


@pytest.mark.asyncio
async def test_a_fiscal_document_keeps_its_own_reference(client: AsyncClient, biz_headers):
    """The key on a nota fiscal is 44 digits and is not our invoice
    number. It is stored on the file that carries it, not on the debt."""
    invoice = await make_invoice(client, biz_headers)
    key = "35260812345678000190550010000012341234567890"
    resp = await upload(
        client, biz_headers, invoice["id"], filename="nfe.pdf",
        kind="fiscal", document_number=key, issued_at=str(TODAY),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["document_number"] == key
    assert body["issued_at"] == str(TODAY)


@pytest.mark.asyncio
async def test_an_unknown_kind_is_refused(client: AsyncClient, biz_headers):
    invoice = await make_invoice(client, biz_headers)
    resp = await upload(client, biz_headers, invoice["id"], kind="nota_fiscal_eletronica")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Which file *is* the document
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_suppliers_bill_becomes_the_document_by_itself(
    client: AsyncClient, biz_headers
):
    """Nobody should have to tell us the supplier's own PDF outranks a
    page we would have drawn from our own fields."""
    invoice = await make_invoice(
        client, biz_headers, direction="payable", origin="imported",
        external_source="email", external_id="ax-1", external_number="2026/A/0031",
    )
    resp = await upload(client, biz_headers, invoice["id"], kind="bill")
    assert resp.json()["is_primary"] is True


@pytest.mark.asyncio
async def test_our_own_invoice_keeps_our_render_as_the_document(
    client: AsyncClient, biz_headers
):
    """We wrote it, so our page is the document and a file filed beside
    it is evidence, not a replacement."""
    invoice = await make_invoice(client, biz_headers)
    resp = await upload(client, biz_headers, invoice["id"], kind="bill")
    assert resp.json()["is_primary"] is False

    doc = await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
    assert doc.json()["source_file"] is None


@pytest.mark.asyncio
async def test_only_one_file_can_be_the_document(client: AsyncClient, biz_headers):
    """Promoting a second one demotes the first rather than failing on the
    partial unique index."""
    invoice = await make_invoice(client, biz_headers)
    first = (await upload(client, biz_headers, invoice["id"], filename="a.pdf", is_primary=True)).json()
    second = (await upload(client, biz_headers, invoice["id"], filename="b.pdf")).json()

    resp = await client.patch(
        f"/api/invoices/{invoice['id']}/attachments/{second['id']}",
        headers=biz_headers,
        json={"is_primary": True},
    )
    assert resp.status_code == 200
    listing = (
        await client.get(f"/api/invoices/{invoice['id']}/attachments", headers=biz_headers)
    ).json()
    primaries = [row["id"] for row in listing if row["is_primary"]]
    assert primaries == [second["id"]]
    assert first["id"] not in primaries


@pytest.mark.asyncio
async def test_the_filed_document_is_what_downloading_hands_over(
    client: AsyncClient, biz_headers
):
    """The whole point of the aggregator: stop reconstructing a page we
    were handed. A render over the top of a real document looks official
    and is not."""
    invoice = await make_invoice(
        client, biz_headers, direction="payable", origin="imported",
        external_source="email", external_id="ax-2",
    )
    marker = PDF + b"%supplier-original\n"
    await upload(client, biz_headers, invoice["id"], filename="do-fornecedor.pdf",
                 data=marker, kind="bill")

    doc = await client.get(f"/api/invoices/{invoice['id']}/document", headers=biz_headers)
    assert doc.json()["source_file"]["filename"] == "do-fornecedor.pdf"

    pdf = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)
    assert pdf.content == marker
    assert "do-fornecedor.pdf" in pdf.headers["content-disposition"]


@pytest.mark.asyncio
async def test_without_a_filed_document_we_still_render_our_own(
    client: AsyncClient, biz_headers
):
    invoice = await make_invoice(client, biz_headers)
    pdf = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    assert pdf.headers["content-type"] == "application/pdf"


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_files_are_scoped_to_their_workspace(
    client: AsyncClient, biz_headers, auth_headers, personal_ws
):
    """A workspace boundary is not a filter someone can forget."""
    invoice = await make_invoice(client, biz_headers)
    attachment = (await upload(client, biz_headers, invoice["id"])).json()

    other = {**auth_headers, "X-Workspace-Id": str(personal_ws.id)}
    resp = await client.get(
        f"/api/invoices/{invoice['id']}/attachments/{attachment['id']}", headers=other
    )
    # 404 rather than 403: a personal workspace has no invoicing module,
    # so the route does not exist for it at all.
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_an_unknown_invoice_is_not_found(client: AsyncClient, biz_headers):
    resp = await upload(client, biz_headers, uuid.uuid4())
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deleting_a_file_removes_it(client: AsyncClient, biz_headers):
    invoice = await make_invoice(client, biz_headers)
    attachment = (await upload(client, biz_headers, invoice["id"])).json()

    resp = await client.delete(
        f"/api/invoices/{invoice['id']}/attachments/{attachment['id']}", headers=biz_headers
    )
    assert resp.status_code == 204

    listing = await client.get(
        f"/api/invoices/{invoice['id']}/attachments", headers=biz_headers
    )
    assert listing.json() == []


@pytest.mark.asyncio
async def test_a_download_returns_the_bytes_that_were_stored(
    client: AsyncClient, biz_headers
):
    invoice = await make_invoice(client, biz_headers)
    payload = PDF + b"%exactly-these-bytes\n"
    attachment = (
        await upload(client, biz_headers, invoice["id"], data=payload)
    ).json()

    resp = await client.get(
        f"/api/invoices/{invoice['id']}/attachments/{attachment['id']}", headers=biz_headers
    )
    assert resp.content == payload


@pytest.mark.asyncio
async def test_an_import_with_nothing_filed_has_no_pdf_to_give(
    client: AsyncClient, biz_headers
):
    """Refused at the API too, not only hidden in the UI: a rule enforced
    in a button is one the next caller does not know about."""
    invoice = await make_invoice(
        client, biz_headers, direction="payable", origin="imported",
        external_source="email", external_id="none-filed",
    )
    resp = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "no_document_filed"

    # Once the real file is filed, it is served.
    await upload(client, biz_headers, invoice["id"], filename="recebida.pdf", kind="bill")
    resp = await client.get(f"/api/invoices/{invoice['id']}/pdf", headers=biz_headers)
    assert resp.status_code == 200
    assert "recebida.pdf" in resp.headers["content-disposition"]


# ---------------------------------------------------------------------------
# Where each file came from
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_folder_records_which_system_produced_each_file(
    client: AsyncClient, biz_headers
):
    """The point of the column: an invoice assembled from three systems is
    otherwise a pile of files with no author."""
    invoice = await make_invoice(client, biz_headers)
    await upload(client, biz_headers, invoice["id"], filename="cobranca.pdf",
                 kind="bill", source="stripe", external_id="in_1AbC")
    await upload(client, biz_headers, invoice["id"], filename="nfe.pdf",
                 kind="fiscal", source="nfe-provider", external_id="35260812345678")
    await upload(client, biz_headers, invoice["id"], filename="anexo.pdf", kind="other")

    rows = (
        await client.get(f"/api/invoices/{invoice['id']}/attachments", headers=biz_headers)
    ).json()
    assert [(r["filename"], r["source"]) for r in rows] == [
        ("cobranca.pdf", "stripe"),
        ("nfe.pdf", "nfe-provider"),
        # A person uploaded this one, and no source is how that is said.
        ("anexo.pdf", None),
    ]
    # Every file records when it reached us, which is not the date on it.
    assert all(r["created_at"] for r in rows)


@pytest.mark.asyncio
async def test_two_hand_uploads_do_not_collide(client: AsyncClient, biz_headers):
    """The uniqueness is per source, and a null source is not a value two
    rows can collide on — otherwise a second manual upload would fail."""
    invoice = await make_invoice(client, biz_headers)
    first = await upload(client, biz_headers, invoice["id"], filename="a.pdf")
    second = await upload(client, biz_headers, invoice["id"], filename="b.pdf")
    assert first.status_code == 201 and second.status_code == 201


@pytest.mark.asyncio
async def test_a_source_can_be_corrected(client: AsyncClient, biz_headers):
    invoice = await make_invoice(client, biz_headers)
    filed = (await upload(client, biz_headers, invoice["id"])).json()
    resp = await client.patch(
        f"/api/invoices/{invoice['id']}/attachments/{filed['id']}",
        headers=biz_headers,
        json={"source": "email"},
    )
    assert resp.json()["source"] == "email"


@pytest.mark.asyncio
async def test_a_resync_of_the_same_file_lands_on_the_row_it_wrote(
    client: AsyncClient, biz_headers
):
    """`source` + `external_id` is unique per workspace, so the second
    sync used to be rejected by the index *after* its bytes were written,
    leaving a blob nothing could reach."""
    invoice = await make_invoice(client, biz_headers)
    first = await upload(
        client, biz_headers, invoice["id"], filename="nfe.pdf",
        kind="fiscal", source="nfe-provider", external_id="35260812345678",
    )
    again = await upload(
        client, biz_headers, invoice["id"], filename="nfe.pdf",
        kind="fiscal", source="nfe-provider", external_id="35260812345678",
    )
    assert again.status_code == 201
    assert again.json()["id"] == first.json()["id"]

    listing = await client.get(
        f"/api/invoices/{invoice['id']}/attachments", headers=biz_headers
    )
    assert len(listing.json()) == 1
