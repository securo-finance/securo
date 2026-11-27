"""Documents read into transactions (ADR 0037).

A statement or a receipt is uploaded, stored under the workspace's prefix and
read in the background. Nothing it read becomes a transaction until a person
confirms it: a statement through the regular import (preview, review,
duplicates skipped), a receipt by attaching it to a transaction that already
exists or by creating one from it.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.account import Account
from app.models.category import Category
from app.models.document_extraction import DocumentExtraction
from app.models.transaction import Transaction
from app.providers import get_storage_provider
from app.schemas.transaction import TransactionCreate, TransactionImport
from app.services import document_ai_settings_service as ai_settings
from app.services.attachment_service import sanitize_filename
from app.services.document_reader import ReaderError, read_document, sniff

logger = logging.getLogger(__name__)

KINDS = ("statement", "receipt")
# A read that has been "processing" this long lost its worker.
STUCK_AFTER = timedelta(minutes=15)

_EXTENSIONS = {"pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_CONTENT_TYPES = {"pdf": "application/pdf", "image/png": "image/png", "image/jpeg": "image/jpeg", "image/webp": "image/webp"}


class ExtractionError(Exception):
    """Refused before anything was stored; `code` is for the UI."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def stored_filename(filename: str, file_type: str) -> str:
    """The upload's name, sanitized, with the extension of what it really is."""
    base = sanitize_filename(filename or "document")
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{stem[:200]}.{_EXTENSIONS[file_type]}"


async def create_extraction(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    kind: str,
    filename: str,
    content: bytes,
) -> DocumentExtraction:
    if kind not in KINDS:
        raise ExtractionError("invalid_kind", f"kind must be one of {', '.join(KINDS)}")
    settings = get_settings()
    max_bytes = settings.document_ai_max_file_size_mb * 1024 * 1024
    if not content:
        raise ExtractionError("empty_document", "The file is empty.")
    if len(content) > max_bytes:
        raise ExtractionError(
            "file_too_large", f"File too large. Maximum size is {settings.document_ai_max_file_size_mb} MB.", 413
        )
    file_type = sniff(content)
    if file_type is None:
        raise ExtractionError("unsupported_file", "Only PDF, PNG, JPEG and WebP files can be read.")

    resolved = await ai_settings.resolve_model(session, workspace_id)
    if not resolved.configured:
        raise ExtractionError("not_configured", "No model is set up to read documents.", 409)
    if file_type != "pdf":
        answer = await ai_settings.vision_for(resolved)
        if answer.vision == "no":
            raise ExtractionError(
                "vision_required",
                f"The model {resolved.model} does not read images. Photos need a model that does.",
                409,
            )

    extraction_id = uuid.uuid4()
    name = stored_filename(filename, file_type)
    storage_key = f"{workspace_id}/documents/{extraction_id}/{name}"
    stored = await get_storage_provider().upload(storage_key, content, _CONTENT_TYPES[file_type])
    extraction = DocumentExtraction(
        id=extraction_id,
        workspace_id=workspace_id,
        user_id=user_id,
        kind=kind,
        status="pending",
        filename=name,
        content_type=stored.content_type,
        size=stored.size,
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=stored.storage_key,
        provider=resolved.provider,
        model=resolved.model,
    )
    session.add(extraction)
    await session.commit()
    await session.refresh(extraction)
    return extraction


async def get_extraction(
    session: AsyncSession, workspace_id: uuid.UUID, extraction_id: uuid.UUID
) -> Optional[DocumentExtraction]:
    extraction = (
        await session.execute(
            select(DocumentExtraction).where(
                DocumentExtraction.id == extraction_id,
                DocumentExtraction.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if extraction is not None and extraction.status in ("pending", "processing"):
        updated = extraction.updated_at
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated is not None and _now() - updated > STUCK_AFTER:
            extraction.status = "failed"
            extraction.error_code = "timeout"
            extraction.error = "Reading took too long and was stopped."
            await session.commit()
            await session.refresh(extraction)
    return extraction


async def mark_unqueued(
    session: AsyncSession, extraction: DocumentExtraction, exc: Exception
) -> DocumentExtraction:
    logger.error("Could not queue document extraction %s: %s", extraction.id, exc)
    extraction.status = "failed"
    extraction.error_code = "queue_unavailable"
    extraction.error = "The background queue is unavailable. Try again in a moment."
    extraction.completed_at = _now()
    await session.commit()
    await session.refresh(extraction)
    return extraction


async def reset_for_retry(session: AsyncSession, extraction: DocumentExtraction) -> DocumentExtraction:
    if extraction.status not in ("failed", "done") or extraction.confirmed_at is not None:
        raise ExtractionError("not_retryable", "Only an unconfirmed read can be read again.", 409)
    resolved = await ai_settings.resolve_model(session, extraction.workspace_id)
    if not resolved.configured:
        raise ExtractionError("not_configured", "No model is set up to read documents.", 409)
    extraction.status = "pending"
    extraction.error = None
    extraction.error_code = None
    extraction.result = None
    extraction.provider = resolved.provider
    extraction.model = resolved.model
    await session.commit()
    await session.refresh(extraction)
    return extraction


async def run_extraction(session: AsyncSession, extraction_id: uuid.UUID) -> None:
    """Read one document. Called by the worker; every outcome is recorded."""
    extraction = (
        await session.execute(select(DocumentExtraction).where(DocumentExtraction.id == extraction_id))
    ).scalar_one_or_none()
    if extraction is None or extraction.status not in ("pending", "processing"):
        return
    extraction.status = "processing"
    await session.commit()

    try:
        resolved = await ai_settings.resolve_model(session, extraction.workspace_id)
        if not resolved.configured:
            raise ReaderError("not_configured", "No model is set up to read documents.")
        content = await get_storage_provider().download(extraction.storage_key)
        answer = await ai_settings.vision_for(resolved)
        provider = ai_settings.build_provider(resolved)
        outcome = await read_document(
            provider=provider,
            model=resolved.model,
            kind="receipt" if extraction.kind == "receipt" else "statement",
            content=content,
            vision=answer.vision,
            max_pages=get_settings().document_ai_max_pages,
            categories=list((await category_choices(session, extraction.workspace_id)).keys()),
        )
    except ReaderError as exc:
        extraction.status = "failed"
        extraction.error_code = exc.code
        extraction.error = exc.message[:2000]
        extraction.completed_at = _now()
        await session.commit()
        return
    except Exception as exc:  # the worker must always leave a final state
        logger.exception("Document extraction %s failed", extraction_id)
        extraction.status = "failed"
        extraction.error_code = "internal_error"
        extraction.error = str(exc)[:2000]
        extraction.completed_at = _now()
        await session.commit()
        return

    result = dict(outcome.result)
    result["usage"] = {"input_tokens": outcome.usage.input_tokens, "output_tokens": outcome.usage.output_tokens}
    result["truncated"] = outcome.pages > get_settings().document_ai_max_pages
    extraction.result = result
    extraction.method = outcome.method
    extraction.pages = outcome.pages
    extraction.provider = resolved.provider
    extraction.model = resolved.model
    extraction.layout_fingerprint = result.get("layout_fingerprint")
    extraction.status = "done"
    extraction.completed_at = _now()
    await session.commit()


# --- Statements ------------------------------------------------------------

def statement_rows(extraction: DocumentExtraction) -> list[TransactionImport]:
    result = extraction.result or {}
    currency = (result.get("statement") or {}).get("currency")
    rows: list[TransactionImport] = []
    for row in result.get("transactions") or []:
        amount = Decimal(row["amount"])
        rows.append(TransactionImport(
            description=row["description"],
            amount=abs(amount),
            date=date.fromisoformat(row["date"]),
            type="credit" if amount > 0 else "debit",
            currency=currency,
            payee_raw=row.get("payee"),
            # The model's pick among the workspace's categories. The import
            # applies it only when no rule categorized the row, and only if
            # it names a category that can take entries.
            category_name=row.get("category"),
        ))
    return rows


async def suggested_account_id(
    session: AsyncSession, extraction: DocumentExtraction
) -> Optional[uuid.UUID]:
    """The account the last confirmed document of the same layout went to."""
    if not extraction.layout_fingerprint:
        return None
    account_id = (
        await session.execute(
            select(DocumentExtraction.account_id)
            .join(Account, Account.id == DocumentExtraction.account_id)
            .where(
                DocumentExtraction.workspace_id == extraction.workspace_id,
                DocumentExtraction.layout_fingerprint == extraction.layout_fingerprint,
                DocumentExtraction.account_id.is_not(None),
                DocumentExtraction.confirmed_at.is_not(None),
                DocumentExtraction.id != extraction.id,
                Account.workspace_id == extraction.workspace_id,
                Account.is_closed.is_(False),
            )
            .order_by(DocumentExtraction.confirmed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return account_id


async def mark_statement_confirmed(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    extraction_id: uuid.UUID,
    account_id: uuid.UUID,
    import_log_id: Optional[uuid.UUID],
    rows: list[TransactionImport],
    commit: bool = True,
) -> None:
    """Record what the person kept, next to what the model read.

    The import calls this with commit=False just before its own commit, so
    the rows and the confirmation land together or not at all. The write is
    conditional on the read not being confirmed yet: of two imports of the
    same document racing, the second raises already_confirmed and its rows
    are rolled back with it.
    """
    confirmed = {
        "transactions": [
            {
                "date": r.date.isoformat(),
                "description": r.description,
                "amount": str(r.amount if r.type == "credit" else -r.amount),
                "excluded": r.excluded,
                "category_id": str(r.category_id) if r.category_id else None,
            }
            for r in rows
        ],
    }
    written = await session.execute(
        update(DocumentExtraction)
        .where(
            DocumentExtraction.id == extraction_id,
            DocumentExtraction.workspace_id == workspace_id,
            DocumentExtraction.kind == "statement",
            DocumentExtraction.confirmed_at.is_(None),
        )
        .values(
            account_id=account_id,
            import_log_id=import_log_id,
            confirmed_by_user_id=user_id,
            confirmed_at=_now(),
            confirmed=confirmed,
        )
    )
    if getattr(written, "rowcount", 0) != 1:
        raise ExtractionError("already_confirmed", "This document was already imported.", 409)
    if commit:
        await session.commit()


async def require_statement_ready(
    session: AsyncSession, workspace_id: uuid.UUID, extraction_id: uuid.UUID
) -> DocumentExtraction:
    extraction = await get_extraction(session, workspace_id, extraction_id)
    if extraction is None or extraction.kind != "statement":
        raise ExtractionError("not_found", "Document not found.", 404)
    if extraction.status != "done":
        raise ExtractionError("not_ready", "The document has not been read yet.", 409)
    if extraction.confirmed_at is not None:
        raise ExtractionError("already_confirmed", "This document was already imported.", 409)
    return extraction


# --- Receipts --------------------------------------------------------------

async def receipt_matches(
    session: AsyncSession, workspace_id: uuid.UUID, extraction: DocumentExtraction, limit: int = 5
) -> list[dict[str, Any]]:
    """Transactions that may be this receipt: same amount, close in time.

    A card purchase usually posts a few days after the receipt's date, so the
    window leans forward.
    """
    result = extraction.result or {}
    if not result.get("total"):
        return []
    total = Decimal(result["total"])
    txn_type = "credit" if result.get("kind") == "refund" else "debit"
    statement = (
        select(Transaction, Account)
        .join(Account, Account.id == Transaction.account_id)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.amount == total,
            Transaction.type == txn_type,
        )
    )
    receipt_day = date.fromisoformat(result["date"]) if result.get("date") else None
    if receipt_day is not None:
        statement = statement.where(
            Transaction.date >= receipt_day - timedelta(days=3),
            Transaction.date <= receipt_day + timedelta(days=10),
        )
    rows = (await session.execute(statement.order_by(Transaction.date.desc()).limit(50))).all()

    attachment_counts: dict[uuid.UUID, int] = {}
    if rows:
        from app.models.transaction_attachment import TransactionAttachment

        counts = await session.execute(
            select(TransactionAttachment.transaction_id, func.count())
            .where(TransactionAttachment.transaction_id.in_([t.id for t, _ in rows]))
            .group_by(TransactionAttachment.transaction_id)
        )
        attachment_counts = {tid: n for tid, n in counts.all()}

    def distance(txn: Transaction) -> int:
        return abs((txn.date - receipt_day).days) if receipt_day else 0

    ranked = sorted(rows, key=lambda pair: (attachment_counts.get(pair[0].id, 0) > 0, distance(pair[0])))
    return [
        {
            "transaction_id": str(txn.id),
            "date": txn.date.isoformat(),
            "description": txn.description,
            "amount": str(txn.amount),
            "currency": txn.currency,
            "type": txn.type,
            "account_id": str(account.id),
            "account_name": account.name,
            "attachments": attachment_counts.get(txn.id, 0),
        }
        for txn, account in ranked[:limit]
    ]


async def receipt_category(
    session: AsyncSession, workspace_id: uuid.UUID, extraction: DocumentExtraction
) -> Optional[uuid.UUID]:
    """A category for a new transaction: the rules first, then the model's
    pick among the workspace's categories, then its one-word hint."""
    from app.services import import_service
    from app.services.category_service import get_assignable_category_ids

    result = extraction.result or {}
    merchant = result.get("merchant")
    if merchant and result.get("total") and result.get("date"):
        probe = TransactionImport(
            description=merchant,
            amount=Decimal(result["total"]),
            date=date.fromisoformat(result["date"]),
            type="credit" if result.get("kind") == "refund" else "debit",
            payee_raw=merchant,
        )
        [enriched] = await import_service.enrich_with_category_suggestions(session, workspace_id, [probe])
        if enriched.suggested_category_id:
            return enriched.suggested_category_id
    picked = (result.get("category") or "").strip().lower()
    if picked:
        choices = {_display_key(name): cid for name, cid in (await category_choices(session, workspace_id)).items()}
        choice = choices.get(_display_key(picked))
        if choice is not None:
            return choice
    hint = (result.get("category_hint") or "").strip().lower()
    if not hint:
        return None
    assignable = await get_assignable_category_ids(session, workspace_id)
    categories = (
        await session.execute(select(Category).where(Category.workspace_id == workspace_id))
    ).scalars().all()
    for category in categories:
        name = category.name.strip().lower()
        if category.id in assignable and (name == hint or hint in name or name in hint):
            return category.id
    return None


def _display_key(name: str) -> str:
    return " ".join(name.split()).lower()


async def category_choices(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """The categories a model may pick, by name: only those that take entries,
    and never a name two categories share (it could not tell them apart)."""
    from app.services.category_service import get_assignable_category_ids

    assignable = await get_assignable_category_ids(session, workspace_id)
    categories = (
        await session.execute(select(Category).where(Category.workspace_id == workspace_id))
    ).scalars().all()
    by_name: dict[str, uuid.UUID] = {}
    display: dict[str, str] = {}
    shared: set[str] = set()
    for category in categories:
        if category.id not in assignable or not category.name.strip():
            continue
        key = _display_key(category.name)
        if key in by_name:
            shared.add(key)
        by_name[key] = category.id
        display[key] = " ".join(category.name.split())
    return {display[k]: by_name[k] for k in sorted(by_name, key=lambda k: display[k]) if k not in shared}


async def _require_receipt_ready(
    session: AsyncSession, workspace_id: uuid.UUID, extraction_id: uuid.UUID
) -> DocumentExtraction:
    extraction = await get_extraction(session, workspace_id, extraction_id)
    if extraction is None or extraction.kind != "receipt":
        raise ExtractionError("not_found", "Document not found.", 404)
    if extraction.status != "done":
        raise ExtractionError("not_ready", "The document has not been read yet.", 409)
    if extraction.confirmed_at is not None:
        raise ExtractionError("already_confirmed", "This receipt was already used.", 409)
    return extraction


async def _attach_file(
    session: AsyncSession,
    extraction: DocumentExtraction,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
) -> None:
    from app.services import attachment_service

    try:
        content = await get_storage_provider().download(extraction.storage_key)
        await attachment_service.upload_attachment(
            session, extraction.workspace_id, user_id, transaction_id, extraction.filename, content,
        )
    except LookupError as exc:
        raise ExtractionError("not_found", "Transaction not found.", 404) from exc
    except ValueError as exc:
        raise ExtractionError("attachment_refused", str(exc)) from exc
    except Exception as exc:  # storage unreachable, and the like
        logger.exception("Attaching document %s failed", extraction.id)
        raise ExtractionError("attachment_refused", "The file could not be stored with the transaction.") from exc


def _check_attachable(extraction: DocumentExtraction) -> None:
    """Refuse before anything is written when the file cannot become an attachment."""
    from app.services import attachment_service

    try:
        attachment_service._validate_file(extraction.filename, extraction.size)
    except ValueError as exc:
        raise ExtractionError("attachment_refused", str(exc)) from exc


async def _claim_receipt(
    session: AsyncSession, workspace_id: uuid.UUID, extraction_id: uuid.UUID, user_id: uuid.UUID
) -> DocumentExtraction:
    """Take the receipt for this request alone, committed before anything else.

    The claim is one conditional UPDATE: of two requests racing, only one
    sees a row change, the other gets already_confirmed. It is committed on
    its own because creating the transaction commits too, which would release
    a row lock taken instead.
    """
    extraction = await _require_receipt_ready(session, workspace_id, extraction_id)
    _check_attachable(extraction)
    claimed = await session.execute(
        update(DocumentExtraction)
        .where(DocumentExtraction.id == extraction.id, DocumentExtraction.confirmed_at.is_(None))
        .values(confirmed_at=_now(), confirmed_by_user_id=user_id)
    )
    await session.commit()
    if getattr(claimed, "rowcount", 0) != 1:
        raise ExtractionError("already_confirmed", "This receipt was already used.", 409)
    await session.refresh(extraction)
    return extraction


async def _release_receipt(session: AsyncSession, extraction: DocumentExtraction) -> None:
    extraction_id = extraction.id  # read before the rollback expires it
    await session.rollback()
    await session.execute(
        update(DocumentExtraction)
        .where(DocumentExtraction.id == extraction_id)
        .values(confirmed_at=None, confirmed_by_user_id=None)
    )
    await session.commit()


async def attach_receipt(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    extraction_id: uuid.UUID,
    transaction_id: uuid.UUID,
) -> DocumentExtraction:
    extraction = await _claim_receipt(session, workspace_id, extraction_id, user_id)
    try:
        await _attach_file(session, extraction, user_id, transaction_id)
    except ExtractionError:
        # Nothing was written: the receipt can be used again.
        await _release_receipt(session, extraction)
        raise
    extraction.transaction_id = transaction_id
    extraction.confirmed = {"action": "attach", "transaction_id": str(transaction_id)}
    await session.commit()
    await session.refresh(extraction)
    return extraction


async def create_transaction_from_receipt(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    extraction_id: uuid.UUID,
    data: TransactionCreate,
) -> tuple[DocumentExtraction, Transaction]:
    from app.services import transaction_service

    extraction = await _claim_receipt(session, workspace_id, extraction_id, user_id)
    if data.is_paid is None:
        # A receipt is the proof the money already moved.
        data = data.model_copy(update={"is_paid": True})
    try:
        transaction = await transaction_service.create_transaction(session, workspace_id, user_id, data)
    except Exception as exc:
        # Nothing was created: release the receipt so it can be used again.
        await _release_receipt(session, extraction)
        if isinstance(exc, ValueError):
            raise ExtractionError("invalid_transaction", str(exc)) from exc
        raise
    attach_error: Optional[ExtractionError] = None
    try:
        await _attach_file(session, extraction, user_id, transaction.id)
    except ExtractionError as exc:
        # Checked beforehand, so this is storage failing. The transaction
        # exists: the response carries it and says the file did not make it.
        attach_error = exc
    extraction.transaction_id = transaction.id
    extraction.account_id = data.account_id
    extraction.confirmed = {
        "action": "create",
        "transaction_id": str(transaction.id),
        "description": data.description,
        "amount": str(data.amount),
        "date": data.date.isoformat(),
        "type": data.type,
        "currency": data.currency,
        "category_id": str(data.category_id) if data.category_id else None,
        "attached": attach_error is None,
    }
    await session.commit()
    await session.refresh(extraction)
    return extraction, transaction


async def delete_extraction(session: AsyncSession, extraction: DocumentExtraction) -> None:
    """Forget a read that was never confirmed, file included."""
    if extraction.confirmed_at is not None:
        raise ExtractionError("already_confirmed", "A confirmed document stays with what it created.", 409)
    try:
        await get_storage_provider().delete(extraction.storage_key)
    except Exception:  # the row goes either way; an orphan file is harmless
        logger.warning("Could not delete stored document %s", extraction.storage_key)
    await session.delete(extraction)
    await session.commit()
