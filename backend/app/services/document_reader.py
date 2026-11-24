"""Reading a document into structured data with an LLM (ADR 0037).

Nothing here touches the database: it takes bytes and a provider and gives
back what the model read, validated. A PDF with text is read from that text,
which any model can do and costs far less than images; a PDF that is only
scans, and every photo, goes to the model as images.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal, Optional

from app.agents.providers.base import ChatMessage, ImagePart, LLMError, LLMProvider, Usage
from app.core.app_clock import app_today
from app.services.document_ai_capabilities import Vision, looks_like_image_refusal

Kind = Literal["statement", "receipt"]

# Text per model call. Statements run long; a page of one is ~3-5k
# characters, so this sends three or four pages at a time.
MAX_CHARS_PER_CHUNK = 14000
IMAGES_PER_CALL = 4
MAX_IMAGE_SIDE = 2000
# Below this many characters per page a PDF is taken to be scans.
MIN_TEXT_PER_PAGE = 60
MAX_OUTPUT_TOKENS = 8192


class ReaderError(Exception):
    """A document that could not be read, with a code the UI translates."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# --- Files -----------------------------------------------------------------

def sniff(content: bytes) -> Optional[str]:
    """pdf, image/png, image/jpeg, image/webp, or None."""
    head = content[:16]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def pdf_text_pages(content: bytes) -> list[str]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            # Many bank PDFs are "encrypted" with an empty user password.
            try:
                if not reader.decrypt(""):
                    raise ReaderError("encrypted_pdf", "The PDF is password protected.")
            except ReaderError:
                raise
            except Exception as exc:
                raise ReaderError("encrypted_pdf", "The PDF is password protected.") from exc
        return [(page.extract_text() or "") for page in reader.pages]
    except ReaderError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise ReaderError("unreadable_file", f"The PDF could not be opened: {exc}") from exc


def page_has_text(page: str) -> bool:
    """A page whose own text can be read; otherwise it is a scan."""
    return len(re.sub(r"\s+", "", page or "")) >= MIN_TEXT_PER_PAGE and bool(re.search(r"\d", page or ""))


def _jpeg_part(image) -> ImagePart:
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=82, optimize=True)
    return ImagePart(media_type="image/jpeg", data=base64.b64encode(buf.getvalue()).decode("ascii"))


def render_pdf_pages(content: bytes, indexes: list[int]) -> list[ImagePart]:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(content)
    except Exception as exc:  # pdfium raises its own PdfiumError
        raise ReaderError("unreadable_file", f"The PDF could not be rendered: {exc}") from exc
    try:
        parts: list[ImagePart] = []
        for index in indexes:
            if index >= len(pdf):
                continue
            page = pdf[index]
            # ~150 dpi: small print on a statement stays legible.
            bitmap = page.render(scale=150 / 72)
            parts.append(_jpeg_part(bitmap.to_pil()))
            page.close()
        return parts
    finally:
        pdf.close()


def normalize_image(content: bytes) -> ImagePart:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(content))
        image = ImageOps.exif_transpose(image)
    except (UnidentifiedImageError, OSError) as exc:
        raise ReaderError("unreadable_file", f"The image could not be opened: {exc}") from exc
    return _jpeg_part(image)


# --- Prompts ---------------------------------------------------------------

_STATEMENT_PROMPT = """You read bank account statements and credit card statements (bills) and return their transactions as JSON.

Return one JSON object, and nothing else, with this shape:
{
  "statement": {
    "kind": "bank" | "credit_card" | "other",
    "institution": string or null,        // the bank or card issuer
    "account_name": string or null,
    "account_last_digits": string or null, // last 4 digits of the account or card, if printed
    "currency": string or null,            // ISO 4217 code, e.g. "BRL", "USD", "EUR"
    "period_start": "YYYY-MM-DD" or null,
    "period_end": "YYYY-MM-DD" or null,
    "opening_balance": number or null,     // bank: balance before the first transaction. card: previous bill's balance
    "closing_balance": number or null      // bank: balance after the last transaction. card: this bill's total due
  },
  "transactions": [
    {"date": "YYYY-MM-DD", "description": string, "amount": number, "payee": string or null, "category": string or null}
  ]
}

Rules for transactions:
- One entry per transaction line, in the order printed. Two identical lines are two transactions: keep both.
- amount is signed. Negative when money leaves the account, or for a purchase, fee, interest or charge on a card. Positive for deposits, income, refunds, and payments made to a card.
- Use the number exactly as printed (no rounding). Use a dot as the decimal separator in JSON, whatever the document uses ("1.234,56" is 1234.56).
- Skip lines that are not transactions: balances ("saldo", "saldo anterior", "balance"), totals, subtotals, headers, limits, interest rate tables, and summaries of future installments.
- Dates must be full dates. When a line prints only day and month, take the year from the statement period; a December line on a statement that ends in January belongs to the previous year.
- For an installment purchase ("parcela 3/10", "3 de 10"), keep the installment marker in the description and use the amount of this installment.
- description is the text of the line, without the date and the amount.
If the document has no transactions, return an empty list. Never invent values: use null when something is not printed."""

_RECEIPT_PROMPT = """You read receipts, invoices and payment proofs (photos or scans) and return the purchase as JSON.

Return one JSON object, and nothing else, with this shape:
{
  "merchant": string or null,          // the store or company that was paid, as printed
  "merchant_tax_id": string or null,   // CNPJ, VAT number or similar, if printed
  "date": "YYYY-MM-DD" or null,        // the purchase date
  "total": number or null,             // the total paid, a positive number
  "currency": string or null,          // ISO 4217 code, e.g. "BRL", "USD", "EUR"
  "kind": "purchase" | "refund",
  "payment_method": string or null,    // e.g. "credit card", "debit card", "cash", "pix"
  "category_hint": string or null,     // one or two words for what was bought, e.g. "groceries", "fuel", "restaurant"
  "category": string or null,          // one of the listed categories, written exactly as listed, or null
  "items": [{"description": string, "amount": number}]   // the lines, when legible; may be empty
}

Use the number exactly as printed, with a dot as the decimal separator in JSON ("1.234,56" is 1234.56). The total is the amount actually paid, after discounts and including taxes and tips. Never invent values: use null when something is not printed or not legible."""


def _categories_instruction(categories: Optional[list[str]]) -> str:
    """The person's own categories, for the model to pick from by name."""
    if not categories:
        return "\n\nThere are no categories to pick from: always use null for \"category\"."
    listed = "\n".join(f"- {name}" for name in categories[:300])
    return (
        "\n\nCategories you may use for \"category\" (the person's own, in their language). "
        "Pick the one that fits what was bought or paid, written exactly as listed. Use null when "
        "none fits or you are unsure; never invent a category. Transfers between the person's own "
        "accounts and card bill payments get null.\n" + listed
    )


# --- Parsing ---------------------------------------------------------------

def parse_json_object(text: str) -> dict:
    """The JSON object in a model's answer, tolerating fences and prose."""
    cleaned = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ReaderError("invalid_response", "The model did not answer with JSON.")
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ReaderError("invalid_response", f"The model's JSON could not be parsed: {exc}") from exc
    if not isinstance(value, dict):
        raise ReaderError("invalid_response", "The model did not answer with a JSON object.")
    return value


_CENT = Decimal("0.01")


def to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        raw = str(value)
    elif isinstance(value, str):
        raw = value.strip().replace(" ", "").replace("\u2212", "-")
        if not raw:
            return None
        # Accounting notation keeps its sign: "(120.50)" and "120.50-" are
        # debits. A sign the text cannot settle is refused, not guessed.
        negative = False
        if raw.startswith("(") and raw.endswith(")"):
            negative, raw = True, raw[1:-1]
        elif raw.endswith("-") and not raw.startswith("-"):
            negative, raw = True, raw[:-1]
        if "(" in raw or ")" in raw or raw.count("-") + raw.count("+") > 1:
            return None
        if raw.startswith("-"):
            if negative:
                return None  # "(-120.50)": two sign markers, meaning unclear
            negative, raw = True, raw[1:]
        raw = raw.lstrip("+")
        if "-" in raw or "+" in raw:
            return None
        # "1.234,56" or "1,234.56": the last separator is the decimal one.
        if "," in raw and "." in raw:
            if raw.rfind(",") > raw.rfind("."):
                raw = raw.replace(".", "").replace(",", ".")
            else:
                raw = raw.replace(",", "")
        elif "," in raw:
            raw = raw.replace(",", ".")
        raw = re.sub(r"[^\d.]", "", raw)
        if negative:
            raw = "-" + raw
    else:
        return None
    try:
        return Decimal(raw).quantize(_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def to_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
    today = app_today()
    if parsed.year < 1990 or parsed > today + timedelta(days=400):
        return None
    return parsed


def _text(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] or None


def _currency(value: Any) -> Optional[str]:
    text = (_text(value, 8) or "").upper()
    return text if re.fullmatch(r"[A-Z]{3}", text) else None


@dataclass
class ReadOutcome:
    result: dict
    method: Literal["text", "vision"]
    pages: int
    usage: Usage = field(default_factory=Usage)


def clean_statement(raw_chunks: list[dict]) -> dict:
    """Merge the chunks' answers into one statement, rows validated."""
    meta: dict[str, Any] = {}
    rows: list[dict] = []
    dropped = 0
    for chunk in raw_chunks:
        raw_statement = chunk.get("statement")
        statement: dict[str, Any] = raw_statement if isinstance(raw_statement, dict) else {}
        for key, value in statement.items():
            if value in (None, ""):
                continue
            # The period's end and the closing balance come from the last
            # chunk that printed them; everything else from the first.
            if key in ("closing_balance", "period_end") or key not in meta:
                meta[key] = value
        for txn in chunk.get("transactions") or []:
            if not isinstance(txn, dict):
                dropped += 1
                continue
            amount = to_decimal(txn.get("amount"))
            day = to_date(txn.get("date"))
            description = _text(txn.get("description"), 500)
            if amount is None or amount == 0 or day is None or not description:
                dropped += 1
                continue
            rows.append({
                "date": day.isoformat(),
                "description": description,
                "amount": str(amount),
                "payee": _text(txn.get("payee"), 255),
                "category": _text(txn.get("category"), 120),
            })
    kind = meta.get("kind") if meta.get("kind") in ("bank", "credit_card", "other") else "other"
    statement = {
        "kind": kind,
        "institution": _text(meta.get("institution"), 120),
        "account_name": _text(meta.get("account_name"), 120),
        "account_last_digits": (re.sub(r"\D", "", str(meta.get("account_last_digits") or ""))[-4:] or None),
        "currency": _currency(meta.get("currency")),
        "period_start": (d.isoformat() if (d := to_date(meta.get("period_start"))) else None),
        "period_end": (d.isoformat() if (d := to_date(meta.get("period_end"))) else None),
        "opening_balance": (str(v) if (v := to_decimal(meta.get("opening_balance"))) is not None else None),
        "closing_balance": (str(v) if (v := to_decimal(meta.get("closing_balance"))) is not None else None),
    }
    return {"statement": statement, "transactions": rows, "dropped": dropped}


def merge_receipt_answers(answers: list[dict]) -> dict:
    """One receipt from several answers (a PDF read in parts), in page order.

    Lines add up across pages. The total and the payment come from the last
    page that printed them, where a receipt closes; everything else from the
    first.
    """
    merged: dict[str, Any] = {"items": []}
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        merged["items"].extend(answer.get("items") or [])
        for key, value in answer.items():
            if key == "items" or value in (None, ""):
                continue
            if key in ("total", "payment_method") or key not in merged:
                merged[key] = value
    return merged


def clean_receipt(raw: dict) -> dict:
    items = []
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        amount = to_decimal(item.get("amount"))
        description = _text(item.get("description"), 200)
        if description and amount is not None:
            items.append({"description": description, "amount": str(amount)})
    total = to_decimal(raw.get("total"))
    day = to_date(raw.get("date"))
    return {
        "merchant": _text(raw.get("merchant"), 255),
        "merchant_tax_id": _text(raw.get("merchant_tax_id"), 40),
        "date": day.isoformat() if day else None,
        "total": str(abs(total)) if total is not None and total != 0 else None,
        "currency": _currency(raw.get("currency")),
        "kind": "refund" if raw.get("kind") == "refund" else "purchase",
        "payment_method": _text(raw.get("payment_method"), 60),
        "category_hint": _text(raw.get("category_hint"), 60),
        "category": _text(raw.get("category"), 120),
        "items": items[:100],
    }


def balance_check(statement_result: dict) -> Optional[dict]:
    """Whether the rows add up to the statement's own balances.

    A bank statement's closing balance is its opening plus every row; a card
    bill's total due is the previous balance plus purchases minus payments,
    which in signed rows is opening minus their sum. None when the document
    printed no balances to check against.
    """
    statement = statement_result.get("statement") or {}
    opening = to_decimal(statement.get("opening_balance"))
    closing = to_decimal(statement.get("closing_balance"))
    if opening is None or closing is None:
        return None
    total = sum((Decimal(r["amount"]) for r in statement_result.get("transactions") or []), Decimal("0"))
    if statement.get("kind") == "credit_card":
        expected = opening - total
    else:
        expected = opening + total
    difference = (closing - expected).quantize(_CENT)
    return {
        "opening_balance": str(opening),
        "closing_balance": str(closing),
        "computed_closing_balance": str(expected.quantize(_CENT)),
        "difference": str(difference),
        "ok": abs(difference) < Decimal("0.01"),
    }


def layout_fingerprint(kind: Kind, statement_result: dict, first_page_text: str) -> Optional[str]:
    """Same issuer and same layout, same value; names and numbers ignored."""
    if kind != "statement":
        return None
    statement = statement_result.get("statement") or {}
    institution = (statement.get("institution") or "").strip().lower()
    if not institution:
        return None
    words = re.findall(r"[^\W\d_]{4,}", (first_page_text or "").lower())[:25]
    basis = "|".join([institution, statement.get("kind") or "", statement.get("account_last_digits") or "", *words])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


# --- Reading ---------------------------------------------------------------

def _chunks(pages: list[str]) -> list[tuple[int, int, str]]:
    """Group text pages into model-sized chunks; empty pages (scans) are skipped."""
    chunks: list[tuple[int, int, str]] = []
    start, last, buf, size = 0, 0, [], 0
    for index, page in enumerate(pages):
        if not page.strip():
            continue  # a scan, read as an image instead
        if buf and size + len(page) > MAX_CHARS_PER_CHUNK:
            chunks.append((start, last, "\n\n".join(buf)))
            buf, size = [], 0
        if not buf:
            start = index
        buf.append(f"--- page {index + 1} ---\n{page}")
        size += len(page)
        last = index
    if buf:
        chunks.append((start, last, "\n\n".join(buf)))
    return chunks


async def _ask(
    provider: LLMProvider, model: str, system: str, user: str, images: list[ImagePart], usage: Usage,
) -> dict:
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user, images=images),
    ]
    try:
        response = await provider.chat(messages, model=model, temperature=0.0, max_tokens=MAX_OUTPUT_TOKENS)
    except LLMError as exc:
        if images and getattr(exc, "status", None) in (400, 404, 415, 422) and looks_like_image_refusal(str(exc)):
            raise ReaderError("vision_required", str(exc)) from exc
        code = getattr(exc, "code", "unknown")
        raise ReaderError(
            "provider_auth" if code == "auth" else "provider_error", str(exc)[:1000]
        ) from exc
    usage.input_tokens += response.usage.input_tokens
    usage.output_tokens += response.usage.output_tokens
    return parse_json_object(response.content)


async def read_document(
    *,
    provider: LLMProvider,
    model: str,
    kind: Kind,
    content: bytes,
    vision: Vision,
    max_pages: int,
    categories: Optional[list[str]] = None,
) -> ReadOutcome:
    file_type = sniff(content)
    if file_type is None:
        raise ReaderError("unsupported_file", "Only PDF, PNG, JPEG and WebP files can be read.")
    system = _STATEMENT_PROMPT if kind == "statement" else _RECEIPT_PROMPT
    system += _categories_instruction(categories)
    usage = Usage()
    answers: list[dict] = []
    first_page_text = ""

    if file_type == "pdf":
        pages = pdf_text_pages(content)
        if not pages:
            raise ReaderError("empty_document", "The PDF has no pages.")
        page_count = len(pages)
        pages = pages[:max_pages]
        first_page_text = pages[0]
        # Each page on its own: a statement can mix text pages with scans.
        is_text = [page_has_text(page) for page in pages]
        if not any(is_text) and vision == "no":
            raise ReaderError("vision_required", "This PDF is scanned and the model does not read images.")
        method: Literal["text", "vision"] = "text"
        unread_pages: list[int] = []
        # Runs of consecutive pages of one kind, read in page order: the
        # answers merge in that order, so the period's end and the closing
        # balance come from the last page, whatever kind it is.
        runs: list[tuple[bool, list[int]]] = []
        for index, text in enumerate(is_text):
            if runs and runs[-1][0] == text:
                runs[-1][1].append(index)
            else:
                runs.append((text, [index]))
        for text, indexes in runs:
            if text:
                run_pages = [pages[i] if i in indexes else "" for i in range(len(pages))]
                for start, end, chunk in _chunks(run_pages):
                    answers.append(await _ask(
                        provider, model, system,
                        f"Pages {start + 1} to {end + 1} of {page_count}. Read them:\n\n{chunk}",
                        [], usage,
                    ))
            elif vision == "no":
                # Scans the model cannot take stay listed, so the review says so.
                unread_pages.extend(i + 1 for i in indexes)
            else:
                images = render_pdf_pages(content, indexes)
                for offset in range(0, len(images), IMAGES_PER_CALL):
                    numbers = indexes[offset : offset + IMAGES_PER_CALL]
                    batch = images[offset : offset + IMAGES_PER_CALL]
                    answers.append(await _ask(
                        provider, model, system,
                        f"Pages {', '.join(str(n + 1) for n in numbers)} of {page_count}, as images. Read them.",
                        batch, usage,
                    ))
                method = "vision"
    else:
        if vision == "no":
            raise ReaderError("vision_required", "The model does not read images.")
        unread_pages = []
        page_count = 1
        answers.append(await _ask(provider, model, system, "Read this document.", [normalize_image(content)], usage))
        method = "vision"

    if kind == "statement":
        result = clean_statement(answers)
        result["balance_check"] = balance_check(result)
        result["layout_fingerprint"] = layout_fingerprint(kind, result, first_page_text)
        result["unread_pages"] = unread_pages
    else:
        result = clean_receipt(merge_receipt_answers(answers))
        result["unread_pages"] = unread_pages
    return ReadOutcome(result=result, method=method, pages=page_count, usage=usage)
