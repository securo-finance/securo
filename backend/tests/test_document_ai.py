"""Reading documents into transactions (ADR 0037)."""
from __future__ import annotations

import io
import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.agents.providers.base import ChatChunk, ChatMessage, ImagePart, LLMProvider, LLMUnavailableError, Usage
from app.core.config import get_settings
from app.models.document_extraction import DocumentExtraction
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.providers.storage import StoredFile
from app.services import document_ai_settings_service as ai_settings
from app.services import document_extraction_service as extraction_service
from app.services import document_reader
from app.services.document_ai_capabilities import resolve_vision, vision_from_name
from app.services.document_reader import ReaderError


# --- Fakes -------------------------------------------------------------------

class MemoryStorage:
    name = "memory"

    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def upload(self, storage_key, data, content_type):
        self.files[storage_key] = data
        return StoredFile(storage_key=storage_key, size=len(data), content_type=content_type)

    async def download(self, storage_key):
        return self.files[storage_key]

    async def delete(self, storage_key):
        self.files.pop(storage_key, None)


class FakeProvider(LLMProvider):
    """Answers with the next canned JSON and records what it was sent."""

    name = "fake"

    def __init__(self, answers: list[dict], error: Optional[Exception] = None):
        super().__init__(api_key="", model="fake")
        self.answers = list(answers)
        self.error = error
        self.calls: list[list[ChatMessage]] = []

    async def chat_stream(self, messages, *, model, tools=None, temperature=0.4, max_tokens=None):
        self.calls.append(messages)
        if self.error:
            raise self.error
        yield ChatChunk(type="text_delta", text="```json\n" + json.dumps(self.answers.pop(0)) + "\n```")
        yield ChatChunk(type="usage", usage=Usage(input_tokens=100, output_tokens=50))
        yield ChatChunk(type="finish", finish_reason="stop")

    async def embed(self, texts, *, model):
        return []


def text_pdf(lines: list[str], pages: int = 1) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    for _ in range(pages):
        y = 800
        for line in lines:
            pdf.drawString(40, y, line)
            y -= 16
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def scanned_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    pdf.rect(50, 50, 300, 300, fill=1)
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


def png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(buf, format="PNG")
    return buf.getvalue()


STATEMENT_LINES = [
    "Banco Exemplo S.A. - Extrato de conta corrente",
    "Periodo 01/03/2026 a 31/03/2026  Conta 12345-6",
    "Saldo anterior 1.000,00",
    "05/03 PIX RECEBIDO JOAO 500,00",
    "10/03 MERCADO BOM PRECO -120,50",
    "Saldo final 1.379,50",
]

STATEMENT_ANSWER = {
    "statement": {
        "kind": "bank", "institution": "Banco Exemplo", "account_last_digits": "123456",
        "currency": "BRL", "period_start": "2026-03-01", "period_end": "2026-03-31",
        "opening_balance": 1000, "closing_balance": "1.379,50",
    },
    "transactions": [
        {"date": "2026-03-05", "description": "PIX RECEBIDO JOAO", "amount": 500, "payee": "Joao"},
        {"date": "2026-03-10", "description": "MERCADO BOM PRECO", "amount": -120.5, "payee": None},
        {"date": "2026-03-11", "description": "", "amount": -3},
        {"date": "not a date", "description": "x", "amount": -3},
    ],
}

RECEIPT_ANSWER = {
    "merchant": "Padaria Central", "merchant_tax_id": "12.345.678/0001-90", "date": "2026-03-10",
    "total": 120.5, "currency": "BRL", "kind": "purchase", "payment_method": "credit card",
    "category_hint": "groceries", "items": [{"description": "Pao", "amount": 10}],
}


@pytest.fixture
def storage():
    store = MemoryStorage()
    with patch.object(extraction_service, "get_storage_provider", return_value=store), \
         patch("app.services.attachment_service.get_storage_provider", return_value=store):
        yield store


@pytest.fixture(autouse=True)
def no_ollama_probe(monkeypatch):
    # Never reach a real Ollama from the tests.
    async def no_probe(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.document_ai_capabilities.ollama_vision", no_probe)


async def make_connection(session, user, *, kind="ollama", model="qwen2.5vl:7b", name="Local",
                          base_url="http://ollama:11434"):
    from app.agents.models.connection import LlmConnection

    conn = LlmConnection(user_id=user.id, name=name, kind=kind, base_url=base_url, default_model=model, extra={})
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return conn


async def turn_on(session, workspace, user, *, model="qwen2.5vl:7b", override=None, vision="auto"):
    conn = await make_connection(session, user, model=model)
    await ai_settings.save_workspace_settings(
        session, workspace.id, user.id, connection_id=conn.id, model=override, vision=vision,
    )
    return conn


@pytest_asyncio.fixture
async def configured(session, test_workspace, test_user):
    return await turn_on(session, test_workspace, test_user)


@pytest.fixture
def no_dispatch():
    with patch("app.tasks.document_tasks.extract_document.delay") as dispatch:
        yield dispatch


def use_provider(provider: FakeProvider):
    return patch.object(ai_settings, "build_provider", return_value=provider)


# --- Capabilities ------------------------------------------------------------

@pytest.mark.parametrize("model, expected", [
    ("gpt-4o-mini", "yes"), ("claude-sonnet-4-5", "yes"), ("gemini-2.5-flash", "yes"),
    ("qwen2.5vl:7b", "yes"), ("llama3.2-vision", "yes"), ("gemma3:4b", "yes"),
    ("qwen2.5:7b", "no"), ("llama3.1:8b", "no"), ("deepseek-r1:14b", "no"), ("gpt-3.5-turbo", "no"),
    ("o3-mini", "no"), ("my-own-finetune", "unknown"), ("", "unknown"),
])
def test_vision_catalog(model, expected):
    assert vision_from_name(model) == expected


async def test_setting_overrides_the_catalog_and_ollama_answers_first(monkeypatch):
    forced = await resolve_vision(provider="openai", base_url="", model="gpt-3.5-turbo", setting="true")
    assert (forced.vision, forced.source) == ("yes", "setting")

    async def says_no(base_url, model, timeout=5.0):
        return "no"

    monkeypatch.setattr("app.services.document_ai_capabilities.ollama_vision", says_no)
    asked = await resolve_vision(provider="ollama", base_url="http://x", model="llava", setting="auto")
    assert (asked.vision, asked.source) == ("no", "provider")


# --- Providers send images -----------------------------------------------------

def test_providers_serialize_images():
    from app.agents.providers import anthropic, ollama, openai

    message = ChatMessage(role="user", content="Read it", images=[ImagePart(media_type="image/jpeg", data="QUJD")])
    oa = openai._serialize_messages([message])[0]["content"]
    assert oa[0] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}
    assert oa[-1] == {"type": "text", "text": "Read it"}
    an = anthropic._serialize_messages([message])[0]["content"]
    assert an[0]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}
    ol = ollama._serialize_messages([message])[0]
    assert ol["images"] == ["QUJD"] and ol["content"] == "Read it"
    # A message without images is unchanged.
    assert openai._serialize_messages([ChatMessage(role="user", content="hi")])[0]["content"] == "hi"


# --- Reader --------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("1.234,56", "1234.56"), ("1,234.56", "1234.56"), ("-120,5", "-120.50"), (12, "12.00"),
    ("R$ 10,00", "10.00"), (None, None), ("", None), (True, None),
])
def test_to_decimal(raw, expected):
    value = document_reader.to_decimal(raw)
    assert (str(value) if value is not None else None) == expected


def test_parse_json_object_tolerates_prose_and_refuses_garbage():
    assert document_reader.parse_json_object('Sure! {"a": 1} hope it helps') == {"a": 1}
    with pytest.raises(ReaderError) as err:
        document_reader.parse_json_object("no json here")
    assert err.value.code == "invalid_response"


def test_balance_check_bank_and_card():
    bank = document_reader.clean_statement([STATEMENT_ANSWER])
    assert len(bank["transactions"]) == 2 and bank["dropped"] == 2
    check = document_reader.balance_check(bank)
    assert check is not None and check["ok"] and check["computed_closing_balance"] == "1379.50"

    card = document_reader.clean_statement([{
        "statement": {"kind": "credit_card", "opening_balance": 300, "closing_balance": 250},
        "transactions": [
            {"date": "2026-03-01", "description": "Pagamento recebido", "amount": 300},
            {"date": "2026-03-02", "description": "Loja", "amount": -200},
        ],
    }])
    card_check = document_reader.balance_check(card)
    assert card_check is not None and card_check["ok"] is False and card_check["difference"] == "50.00"
    assert document_reader.balance_check({"statement": {}, "transactions": []}) is None


def test_chunks_keep_lines_once_and_merge_period_end():
    merged = document_reader.clean_statement([
        {"statement": {"period_end": "2026-03-15", "closing_balance": 10, "institution": "A"},
         "transactions": [{"date": "2026-03-01", "description": "x", "amount": -1}]},
        {"statement": {"period_end": "2026-03-31", "closing_balance": 20, "institution": "B"},
         "transactions": [{"date": "2026-03-01", "description": "x", "amount": -1}]},
    ])
    # Two identical lines are two transactions.
    assert len(merged["transactions"]) == 2
    assert merged["statement"]["period_end"] == "2026-03-31"
    assert merged["statement"]["closing_balance"] == "20.00"
    assert merged["statement"]["institution"] == "A"


async def test_read_text_pdf_sends_no_images():
    provider = FakeProvider([STATEMENT_ANSWER])
    outcome = await document_reader.read_document(
        provider=provider, model="m", kind="statement", content=text_pdf(STATEMENT_LINES), vision="no", max_pages=5,
    )
    assert outcome.method == "text"
    user = provider.calls[0][1]
    assert not user.images and "MERCADO BOM PRECO" in (user.content or "")
    assert outcome.result["balance_check"]["ok"] is True
    assert outcome.result["layout_fingerprint"]


async def test_scanned_pdf_goes_as_images_or_needs_vision():
    with pytest.raises(ReaderError) as err:
        await document_reader.read_document(
            provider=FakeProvider([]), model="m", kind="statement", content=scanned_pdf(), vision="no", max_pages=5,
        )
    assert err.value.code == "vision_required"

    provider = FakeProvider([STATEMENT_ANSWER])
    outcome = await document_reader.read_document(
        provider=provider, model="m", kind="statement", content=scanned_pdf(), vision="unknown", max_pages=5,
    )
    assert outcome.method == "vision"
    assert provider.calls[0][1].images[0].media_type == "image/jpeg"


async def test_a_refused_image_reads_as_vision_required():
    refused = LLMUnavailableError("OpenAI 400: this model does not support image_url content", status=400)
    with pytest.raises(ReaderError) as err:
        await document_reader.read_document(
            provider=FakeProvider([], error=refused), model="m", kind="receipt", content=png_bytes(),
            vision="unknown", max_pages=5,
        )
    assert err.value.code == "vision_required"


async def test_unsupported_file():
    with pytest.raises(ReaderError) as err:
        await document_reader.read_document(
            provider=FakeProvider([]), model="m", kind="receipt", content=b"hello", vision="yes", max_pages=5,
        )
    assert err.value.code == "unsupported_file"


# --- Settings ------------------------------------------------------------------

async def test_routes_are_closed_while_agents_are_off(client, auth_headers, monkeypatch):
    monkeypatch.setenv("AGENTS_ENABLED", "false")
    assert (await client.get("/api/document-ai/status", headers=auth_headers)).status_code == 404
    assert (await client.get("/api/info")).json()["features"]["document_ai"] is False


async def test_reading_is_off_until_a_connection_is_picked(client, auth_headers, storage):
    status = (await client.get("/api/document-ai/status", headers=auth_headers)).json()
    assert not status["enabled"] and status["connection_id"] is None
    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("s.pdf", text_pdf(STATEMENT_LINES), "application/pdf")}, data={"kind": "statement"},
    )
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "not_configured"
    assert storage.files == {}


async def test_owner_picks_one_of_their_own_connections(client, auth_headers, session, test_user, test_workspace):
    from app.models.user import User

    mine = await make_connection(session, test_user, model="llama3.2-vision", name="Home server")
    stranger = User(email="other@example.com", hashed_password="x", is_active=True, is_verified=True)
    session.add(stranger)
    await session.commit()
    theirs = await make_connection(session, stranger, name="Not yours")

    refused = await client.put(
        "/api/document-ai/settings", headers=auth_headers, json={"connection_id": str(theirs.id)},
    )
    assert refused.status_code == 404
    picked = await client.put(
        "/api/document-ai/settings", headers=auth_headers, json={"connection_id": str(mine.id), "vision": "auto"},
    )
    assert picked.status_code == 200 and picked.json()["connection_id"] == str(mine.id)
    status = (await client.get("/api/document-ai/status", headers=auth_headers)).json()
    assert status["enabled"] and status["connection_name"] == "Home server"
    assert (status["model"], status["vision"], status["vision_source"]) == ("llama3.2-vision", "yes", "catalog")

    # A model of its own for document reading, without touching the connection.
    await client.put(
        "/api/document-ai/settings", headers=auth_headers,
        json={"connection_id": str(mine.id), "model": "llama3.1:8b"},
    )
    status = (await client.get("/api/document-ai/status", headers=auth_headers)).json()
    assert (status["model"], status["vision"]) == ("llama3.1:8b", "no")

    off = await client.put("/api/document-ai/settings", headers=auth_headers, json={"connection_id": None})
    assert off.status_code == 200
    assert (await client.get("/api/document-ai/status", headers=auth_headers)).json()["enabled"] is False


async def test_a_deleted_connection_turns_reading_off(session, test_workspace, test_user):
    conn = await turn_on(session, test_workspace, test_user)
    assert (await ai_settings.resolve_model(session, test_workspace.id)).configured
    await session.delete(conn)
    await session.commit()
    assert not (await ai_settings.resolve_model(session, test_workspace.id)).configured


async def test_a_photo_is_refused_up_front_for_a_text_only_model(
    client, auth_headers, session, storage, test_workspace, test_user,
):
    await turn_on(session, test_workspace, test_user, model="llama3.1:8b")
    status = (await client.get("/api/document-ai/status", headers=auth_headers)).json()
    assert (status["vision"], status["vision_source"]) == ("no", "catalog")
    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("r.png", png_bytes(), "image/png")}, data={"kind": "receipt"},
    )
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "vision_required"


async def test_viewer_cannot_upload_and_settings_are_owner_only(client, viewer_auth_headers, configured, storage):
    resp = await client.post(
        "/api/document-ai/extractions", headers=viewer_auth_headers,
        files={"file": ("s.pdf", text_pdf(STATEMENT_LINES), "application/pdf")}, data={"kind": "statement"},
    )
    assert resp.status_code == 403
    assert (await client.get("/api/document-ai/settings", headers=viewer_auth_headers)).status_code == 403


async def test_statement_end_to_end(
    client, auth_headers, configured, storage, no_dispatch, session, test_workspace, test_account,
):
    pdf = text_pdf(STATEMENT_LINES)
    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("Extrato Março.pdf", pdf, "application/pdf")}, data={"kind": "statement"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending" and body["filename"].endswith(".pdf")
    extraction_id = uuid.UUID(body["id"])
    no_dispatch.assert_called_once_with(str(extraction_id))
    assert all(key.startswith(f"{test_workspace.id}/documents/") for key in storage.files)

    with use_provider(FakeProvider([STATEMENT_ANSWER])):
        await extraction_service.run_extraction(session, extraction_id)

    read = (await client.get(f"/api/document-ai/extractions/{extraction_id}", headers=auth_headers)).json()
    assert read["status"] == "done" and read["method"] == "text"
    assert [t["type"] for t in read["transactions"]] == ["credit", "debit"]
    assert read["transactions"][1]["amount"] == "120.50"
    assert read["balance_check"]["ok"] is True and read["dropped"] == 2
    assert read["suggested_account_id"] is None

    payload = {
        "account_id": str(test_account.id), "filename": read["filename"], "detected_format": "pdf",
        "document_extraction_id": str(extraction_id),
        "transactions": [
            {**read["transactions"][0], "excluded": False},
            {**read["transactions"][1], "description": "Mercado (corrigido)", "excluded": False},
        ],
    }
    imported = await client.post("/api/transactions/import", headers=auth_headers, json=payload)
    assert imported.status_code == 201, imported.text
    assert imported.json()["imported"] == 2

    stored = (await session.execute(select(DocumentExtraction).where(DocumentExtraction.id == extraction_id))).scalar_one()
    await session.refresh(stored)
    assert stored.confirmed_at is not None and stored.account_id == test_account.id
    assert stored.confirmed["transactions"][1]["description"] == "Mercado (corrigido)"
    assert stored.confirmed["transactions"][1]["amount"] == "-120.50"

    again = await client.post("/api/transactions/import", headers=auth_headers, json=payload)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_confirmed"

    # The next statement of the same layout suggests the same account.
    second = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("abril.pdf", pdf, "application/pdf")}, data={"kind": "statement"},
    )
    second_id = uuid.UUID(second.json()["id"])
    with use_provider(FakeProvider([STATEMENT_ANSWER])):
        await extraction_service.run_extraction(session, second_id)
    read2 = (await client.get(f"/api/document-ai/extractions/{second_id}", headers=auth_headers)).json()
    assert read2["suggested_account_id"] == str(test_account.id)


async def test_failed_read_is_recorded_and_retryable(client, auth_headers, configured, storage, no_dispatch, session):
    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("s.pdf", text_pdf(STATEMENT_LINES), "application/pdf")}, data={"kind": "statement"},
    )
    extraction_id = uuid.UUID(resp.json()["id"])
    with use_provider(FakeProvider([], error=LLMUnavailableError("Ollama unreachable"))):
        await extraction_service.run_extraction(session, extraction_id)
    read = (await client.get(f"/api/document-ai/extractions/{extraction_id}", headers=auth_headers)).json()
    assert read["status"] == "failed" and read["error_code"] == "provider_error"

    retried = await client.post(f"/api/document-ai/extractions/{extraction_id}/retry", headers=auth_headers)
    assert retried.status_code == 200 and retried.json()["status"] == "pending"
    assert no_dispatch.call_count == 2

    deleted = await client.delete(f"/api/document-ai/extractions/{extraction_id}", headers=auth_headers)
    assert deleted.status_code == 204 and storage.files == {}


async def test_receipt_matches_and_attaches(
    client, auth_headers, configured, storage, no_dispatch, session, test_account, test_user,
):
    existing = Transaction(
        user_id=test_user.id, workspace_id=test_account.workspace_id, account_id=test_account.id,
        description="PADARIA CENTRAL", amount=Decimal("120.50"), currency="BRL", date=date(2026, 3, 12),
        type="debit", source="manual",
    )
    far = Transaction(
        user_id=test_user.id, workspace_id=test_account.workspace_id, account_id=test_account.id,
        description="Other", amount=Decimal("120.50"), currency="BRL", date=date(2026, 5, 1),
        type="debit", source="manual",
    )
    session.add_all([existing, far])
    await session.commit()

    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("recibo.png", png_bytes(), "image/png")}, data={"kind": "receipt"},
    )
    assert resp.status_code == 201, resp.text
    extraction_id = uuid.UUID(resp.json()["id"])
    with use_provider(FakeProvider([RECEIPT_ANSWER])):
        await extraction_service.run_extraction(session, extraction_id)

    read = (await client.get(f"/api/document-ai/extractions/{extraction_id}", headers=auth_headers)).json()
    assert read["receipt"]["merchant"] == "Padaria Central" and read["receipt"]["total"] == "120.50"
    assert [m["transaction_id"] for m in read["matches"]] == [str(existing.id)]

    attached = await client.post(
        f"/api/document-ai/extractions/{extraction_id}/attach", headers=auth_headers,
        json={"transaction_id": str(existing.id)},
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["transaction_id"] == str(existing.id)
    files = (await session.execute(
        select(TransactionAttachment).where(TransactionAttachment.transaction_id == existing.id)
    )).scalars().all()
    assert len(files) == 1 and files[0].filename.endswith(".png")

    twice = await client.post(
        f"/api/document-ai/extractions/{extraction_id}/attach", headers=auth_headers,
        json={"transaction_id": str(existing.id)},
    )
    assert twice.status_code == 409


async def test_receipt_creates_a_transaction(
    client, auth_headers, configured, storage, no_dispatch, session, test_account,
):
    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("recibo.jpg", png_bytes(), "image/png")}, data={"kind": "receipt"},
    )
    extraction_id = uuid.UUID(resp.json()["id"])
    with use_provider(FakeProvider([RECEIPT_ANSWER])):
        await extraction_service.run_extraction(session, extraction_id)

    created = await client.post(
        f"/api/document-ai/extractions/{extraction_id}/transaction", headers=auth_headers,
        json={
            "account_id": str(test_account.id), "description": "Padaria Central", "amount": "120.50",
            "date": "2026-03-10", "type": "debit", "currency": "BRL",
        },
    )
    assert created.status_code == 200, created.text
    transaction_id = uuid.UUID(created.json()["transaction_id"])
    txn = (await session.execute(select(Transaction).where(Transaction.id == transaction_id))).scalar_one()
    assert txn.description == "Padaria Central" and txn.is_paid is True
    files = (await session.execute(
        select(TransactionAttachment).where(TransactionAttachment.transaction_id == transaction_id)
    )).scalars().all()
    assert len(files) == 1


async def test_another_workspace_cannot_read_it(
    client, auth_headers, configured, storage, no_dispatch, session, test_user,
):
    from app.models.document_extraction import DocumentExtraction as Row
    from app.models.workspace import Workspace

    other = Workspace(name="Other", created_by_user_id=test_user.id)
    session.add(other)
    await session.flush()
    foreign = Row(
        workspace_id=other.id, user_id=test_user.id, kind="statement", status="done", filename="x.pdf",
        content_type="application/pdf", size=1, sha256="0" * 64, storage_key=f"{other.id}/documents/x.pdf",
    )
    session.add(foreign)
    await session.commit()
    resp = await client.get(f"/api/document-ai/extractions/{foreign.id}", headers=auth_headers)
    assert resp.status_code == 404


async def test_import_preview_refuses_a_pdf_clearly(client, auth_headers):
    resp = await client.post(
        "/api/transactions/import/preview", headers=auth_headers,
        files={"file": ("extrato.pdf", text_pdf(STATEMENT_LINES), "application/pdf")},
    )
    assert resp.status_code == 400
    assert "document reading" in resp.json()["detail"]


# --- Review fixes -------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("(120.50)", "-120.50"), ("120.50-", "-120.50"), ("−12,30", "-12.30"), ("-(5)", None),
    ("(1.234,56)", "-1234.56"), ("+7", "7.00"), ("1-2", None), ("(12)-", None),
])
def test_accounting_amounts_keep_their_sign(raw, expected):
    value = document_reader.to_decimal(raw)
    assert (str(value) if value is not None else None) == expected


def mixed_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    y = 800
    for line in STATEMENT_LINES:
        pdf.drawString(40, y, line)
        y -= 16
    pdf.showPage()
    pdf.rect(50, 50, 300, 300, fill=1)
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


async def test_a_scanned_page_in_a_text_pdf_is_read_as_an_image():
    provider = FakeProvider([STATEMENT_ANSWER, {"transactions": []}])
    outcome = await document_reader.read_document(
        provider=provider, model="m", kind="statement", content=mixed_pdf(), vision="yes", max_pages=5,
    )
    text_call, image_call = provider.calls
    assert not text_call[1].images and "page 1" in (text_call[1].content or "")
    assert "page 2" not in (text_call[1].content or "")
    assert len(image_call[1].images) == 1 and "Pages 2 of 2" in (image_call[1].content or "")
    assert outcome.method == "vision" and outcome.result["unread_pages"] == []


async def test_a_scanned_page_a_text_model_cannot_read_is_reported():
    provider = FakeProvider([STATEMENT_ANSWER])
    outcome = await document_reader.read_document(
        provider=provider, model="m", kind="statement", content=mixed_pdf(), vision="no", max_pages=5,
    )
    assert len(provider.calls) == 1
    assert outcome.method == "text" and outcome.result["unread_pages"] == [2]


async def test_a_queue_that_is_down_leaves_a_read_to_retry(client, auth_headers, configured, storage):
    with patch("app.tasks.document_tasks.extract_document.delay", side_effect=ConnectionError("redis down")):
        resp = await client.post(
            "/api/document-ai/extractions", headers=auth_headers,
            files={"file": ("s.pdf", text_pdf(STATEMENT_LINES), "application/pdf")}, data={"kind": "statement"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert (body["status"], body["error_code"]) == ("failed", "queue_unavailable")
    with patch("app.tasks.document_tasks.extract_document.delay") as delay:
        retried = await client.post(f"/api/document-ai/extractions/{body['id']}/retry", headers=auth_headers)
    assert retried.json()["status"] == "pending"
    delay.assert_called_once_with(body["id"])


async def test_the_worker_rechecks_the_switch_before_reading(
    client, auth_headers, configured, storage, no_dispatch, session, monkeypatch,
):
    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("s.pdf", text_pdf(STATEMENT_LINES), "application/pdf")}, data={"kind": "statement"},
    )
    extraction_id = uuid.UUID(resp.json()["id"])
    monkeypatch.setenv("AGENTS_ENABLED", "false")
    provider = FakeProvider([STATEMENT_ANSWER])
    with use_provider(provider):
        await extraction_service.run_extraction(session, extraction_id)
    assert provider.calls == []
    row = (await session.execute(select(DocumentExtraction).where(DocumentExtraction.id == extraction_id))).scalar_one()
    await session.refresh(row)
    assert (row.status, row.error_code) == ("failed", "not_configured")


async def _read_receipt(client, auth_headers, session):
    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("recibo.png", png_bytes(), "image/png")}, data={"kind": "receipt"},
    )
    extraction_id = uuid.UUID(resp.json()["id"])
    with use_provider(FakeProvider([RECEIPT_ANSWER])):
        await extraction_service.run_extraction(session, extraction_id)
    return extraction_id


def _new_transaction(account) -> dict:
    return {
        "account_id": str(account.id), "description": "Padaria Central", "amount": "120.50",
        "date": "2026-03-10", "type": "debit", "currency": "BRL",
    }


async def test_a_receipt_can_be_claimed_once(
    client, auth_headers, configured, storage, no_dispatch, session, test_workspace, test_user,
):
    extraction_id = await _read_receipt(client, auth_headers, session)
    await extraction_service._claim_receipt(session, test_workspace.id, extraction_id, test_user.id)
    with pytest.raises(extraction_service.ExtractionError) as err:
        await extraction_service._claim_receipt(session, test_workspace.id, extraction_id, test_user.id)
    assert err.value.code == "already_confirmed"


async def test_a_failed_creation_releases_the_receipt(
    client, auth_headers, configured, storage, no_dispatch, session, test_account,
):
    extraction_id = await _read_receipt(client, auth_headers, session)
    bad = await client.post(
        f"/api/document-ai/extractions/{extraction_id}/transaction", headers=auth_headers,
        json={**_new_transaction(test_account), "account_id": str(uuid.uuid4())},
    )
    assert bad.status_code >= 400
    good = await client.post(
        f"/api/document-ai/extractions/{extraction_id}/transaction", headers=auth_headers,
        json=_new_transaction(test_account),
    )
    assert good.status_code == 200, good.text


async def test_a_file_too_large_to_attach_is_refused_before_anything_is_created(
    client, auth_headers, configured, storage, no_dispatch, session, test_account, monkeypatch,
):
    extraction_id = await _read_receipt(client, auth_headers, session)
    monkeypatch.setattr(get_settings(), "storage_max_file_size_mb", 0)
    before = (await session.execute(select(func.count()).select_from(Transaction))).scalar_one()
    resp = await client.post(
        f"/api/document-ai/extractions/{extraction_id}/transaction", headers=auth_headers,
        json=_new_transaction(test_account),
    )
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "attachment_refused"
    assert (await session.execute(select(func.count()).select_from(Transaction))).scalar_one() == before


async def test_storage_failing_after_creation_still_returns_the_transaction(
    client, auth_headers, configured, storage, no_dispatch, session, test_account,
):
    extraction_id = await _read_receipt(client, auth_headers, session)
    with patch("app.services.attachment_service.upload_attachment", side_effect=RuntimeError("bucket down")):
        resp = await client.post(
            f"/api/document-ai/extractions/{extraction_id}/transaction", headers=auth_headers,
            json=_new_transaction(test_account),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["transaction_id"] and body["attached"] is False
    again = await client.post(
        f"/api/document-ai/extractions/{extraction_id}/transaction", headers=auth_headers,
        json=_new_transaction(test_account),
    )
    assert again.status_code == 409


def text_scan_text_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    for page in range(3):
        if page == 1:
            pdf.rect(50, 50, 300, 300, fill=1)
        else:
            y = 800
            for line in STATEMENT_LINES:
                pdf.drawString(40, y, f"{line} p{page + 1}")
                y -= 16
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


async def test_mixed_pages_are_read_in_page_order_so_the_last_balance_wins():
    first = {"statement": {"kind": "bank", "closing_balance": 1}, "transactions": []}
    middle = {"statement": {"closing_balance": 2}, "transactions": []}
    last = {"statement": {"closing_balance": 3}, "transactions": []}
    provider = FakeProvider([first, middle, last])
    outcome = await document_reader.read_document(
        provider=provider, model="m", kind="statement", content=text_scan_text_pdf(), vision="yes", max_pages=5,
    )
    first_call, image_call, last_call = provider.calls
    assert "--- page 1 ---" in (first_call[1].content or "") and not first_call[1].images
    assert "Pages 2 of 3" in (image_call[1].content or "") and image_call[1].images
    assert "--- page 3 ---" in (last_call[1].content or "") and "--- page 1 ---" not in (last_call[1].content or "")
    assert outcome.result["statement"]["closing_balance"] == "3.00"


async def test_a_statement_can_be_confirmed_once(
    client, auth_headers, configured, storage, no_dispatch, session, test_workspace, test_user, test_account,
):
    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("s.pdf", text_pdf(STATEMENT_LINES), "application/pdf")}, data={"kind": "statement"},
    )
    extraction_id = uuid.UUID(resp.json()["id"])
    with use_provider(FakeProvider([STATEMENT_ANSWER])):
        await extraction_service.run_extraction(session, extraction_id)
    rows = extraction_service.statement_rows(
        (await session.execute(select(DocumentExtraction).where(DocumentExtraction.id == extraction_id))).scalar_one()
    )
    async def confirm(commit: bool):
        await extraction_service.mark_statement_confirmed(
            session, workspace_id=test_workspace.id, user_id=test_user.id, extraction_id=extraction_id,
            account_id=test_account.id, import_log_id=None, rows=rows, commit=commit,
        )

    await confirm(True)
    # The second one lost the race: its import is rolled back with it.
    with pytest.raises(extraction_service.ExtractionError) as err:
        await confirm(False)
    assert err.value.code == "already_confirmed"


def test_two_sign_markers_are_refused():
    assert document_reader.to_decimal("(-120.50)") is None


async def test_a_receipt_pdf_with_a_scanned_page_keeps_what_each_page_said():
    first = {"merchant": "Padaria Central", "date": "2026-03-10", "items": [{"description": "Pao", "amount": 10}]}
    second = {"total": 120.5, "currency": "BRL", "items": [{"description": "Bolo", "amount": 110.5}]}
    provider = FakeProvider([first, second])
    outcome = await document_reader.read_document(
        provider=provider, model="m", kind="receipt", content=mixed_pdf(), vision="yes", max_pages=5,
    )
    receipt = outcome.result
    assert (receipt["merchant"], receipt["total"], receipt["currency"]) == ("Padaria Central", "120.50", "BRL")
    assert [i["description"] for i in receipt["items"]] == ["Pao", "Bolo"]

    text_only = await document_reader.read_document(
        provider=FakeProvider([first]), model="m", kind="receipt", content=mixed_pdf(), vision="no", max_pages=5,
    )
    assert text_only.result["unread_pages"] == [2]


# --- Categories ---------------------------------------------------------------

async def test_the_model_picks_among_the_workspace_categories(
    client, auth_headers, configured, storage, no_dispatch, session, test_workspace, test_categories,
):
    names = list((await extraction_service.category_choices(session, test_workspace.id)).keys())
    assert names, "the fixture workspace has categories"
    picked = names[0]

    answer = json.loads(json.dumps(STATEMENT_ANSWER))
    answer["transactions"][1]["category"] = picked
    answer["transactions"][0]["category"] = "Something I made up"
    provider = FakeProvider([answer])
    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("s.pdf", text_pdf(STATEMENT_LINES), "application/pdf")}, data={"kind": "statement"},
    )
    extraction_id = uuid.UUID(resp.json()["id"])
    with use_provider(provider):
        await extraction_service.run_extraction(session, extraction_id)

    system = provider.calls[0][0].content or ""
    assert f"- {picked}" in system and "never invent a category" in system
    read = (await client.get(f"/api/document-ai/extractions/{extraction_id}", headers=auth_headers)).json()
    rows = read["transactions"]
    assert rows[1]["suggested_category_name"] == picked
    # A name that is not one of the workspace's categories is dropped.
    assert rows[0]["suggested_category_id"] is None


async def test_a_receipt_takes_the_models_category_when_no_rule_matches(
    client, auth_headers, configured, storage, no_dispatch, session, test_workspace, test_categories,
):
    choices = await extraction_service.category_choices(session, test_workspace.id)
    picked, picked_id = next(iter(choices.items()))
    resp = await client.post(
        "/api/document-ai/extractions", headers=auth_headers,
        files={"file": ("recibo.png", png_bytes(), "image/png")}, data={"kind": "receipt"},
    )
    extraction_id = uuid.UUID(resp.json()["id"])
    with use_provider(FakeProvider([{**RECEIPT_ANSWER, "merchant": "Loja Sem Regra 123", "category": picked.upper()}])):
        await extraction_service.run_extraction(session, extraction_id)
    read = (await client.get(f"/api/document-ai/extractions/{extraction_id}", headers=auth_headers)).json()
    assert read["suggested_category_id"] == str(picked_id)


def test_no_categories_means_no_category():
    assert "always use null" in document_reader._categories_instruction([])
