import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.providers import get_storage_provider


def sanitize_filename(filename: str) -> str:
    """Strip special characters from a filename, preserving the extension."""
    if "." in filename:
        name, ext = filename.rsplit(".", 1)
        ext = re.sub(r"[^a-zA-Z0-9]", "", ext).lower()
    else:
        name = filename
        ext = ""
    # Replace non-alphanumeric (except hyphens/underscores) with underscores, collapse runs
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        name = "file"
    return f"{name}.{ext}" if ext else name


def _validate_file(filename: str, content_type: str, size: int) -> None:
    settings = get_settings()

    max_bytes = settings.storage_max_file_size_mb * 1024 * 1024
    if size > max_bytes:
        raise ValueError(f"File too large. Maximum size is {settings.storage_max_file_size_mb} MB.")

    allowed = {ext.strip().lower() for ext in settings.storage_allowed_extensions.split(",")}
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in allowed:
        raise ValueError(
            f"File type '.{ext}' is not allowed. Allowed: {', '.join(sorted(allowed))}"
        )


async def _verify_transaction_in_workspace(
    session: AsyncSession, transaction_id: uuid.UUID, workspace_id: uuid.UUID
) -> Transaction:
    result = await session.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.workspace_id == workspace_id,
        )
    )
    transaction = result.scalar_one_or_none()
    if not transaction:
        raise LookupError("Transaction not found")
    return transaction


async def upload_attachment(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
    filename: str,
    content_type: str,
    data: bytes,
) -> TransactionAttachment:
    filename = sanitize_filename(filename)
    _validate_file(filename, content_type, len(data))
    await _verify_transaction_in_workspace(session, transaction_id, workspace_id)

    settings = get_settings()
    count_result = await session.execute(
        select(func.count()).where(
            TransactionAttachment.transaction_id == transaction_id,
            TransactionAttachment.workspace_id == workspace_id,
        )
    )
    current_count = count_result.scalar_one()
    if current_count >= settings.storage_max_attachments_per_transaction:
        raise ValueError(
            f"Maximum of {settings.storage_max_attachments_per_transaction} attachments per transaction reached."
        )

    prefix = uuid.uuid4().hex[:8]
    storage_key = f"{workspace_id}/{transaction_id}/{prefix}_{filename}"

    storage = get_storage_provider()
    stored = await storage.upload(storage_key, data, content_type)

    attachment = TransactionAttachment(
        workspace_id=workspace_id,
        user_id=user_id,
        transaction_id=transaction_id,
        filename=filename,
        storage_key=stored.storage_key,
        content_type=stored.content_type,
        size=stored.size,
    )
    session.add(attachment)
    await session.commit()
    await session.refresh(attachment)
    return attachment


async def list_attachments(
    session: AsyncSession, workspace_id: uuid.UUID, transaction_id: uuid.UUID
) -> list[TransactionAttachment]:
    await _verify_transaction_in_workspace(session, transaction_id, workspace_id)
    result = await session.execute(
        select(TransactionAttachment)
        .where(
            TransactionAttachment.transaction_id == transaction_id,
            TransactionAttachment.workspace_id == workspace_id,
        )
        .order_by(TransactionAttachment.created_at)
    )
    return list(result.scalars().all())


async def download_attachment(
    session: AsyncSession, attachment_id: uuid.UUID, workspace_id: uuid.UUID
) -> tuple[TransactionAttachment, bytes]:
    result = await session.execute(
        select(TransactionAttachment).where(
            TransactionAttachment.id == attachment_id,
            TransactionAttachment.workspace_id == workspace_id,
        )
    )
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise LookupError("Attachment not found")

    storage = get_storage_provider()
    data = await storage.download(attachment.storage_key)
    return attachment, data


async def rename_attachment(
    session: AsyncSession,
    attachment_id: uuid.UUID,
    workspace_id: uuid.UUID,
    new_filename: str,
) -> TransactionAttachment:
    new_filename = sanitize_filename(new_filename)
    if not new_filename:
        raise ValueError("Filename cannot be empty.")

    result = await session.execute(
        select(TransactionAttachment).where(
            TransactionAttachment.id == attachment_id,
            TransactionAttachment.workspace_id == workspace_id,
        )
    )
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise LookupError("Attachment not found")

    # Preserve the original extension
    original_ext = (
        attachment.filename.rsplit(".", 1)[-1].lower() if "." in attachment.filename else ""
    )
    new_ext = new_filename.rsplit(".", 1)[-1].lower() if "." in new_filename else ""

    if original_ext and new_ext != original_ext:
        # Strip any wrong extension the user may have typed, re-append original
        name_part = new_filename.rsplit(".", 1)[0] if "." in new_filename else new_filename
        new_filename = f"{name_part}.{original_ext}"

    attachment.filename = new_filename
    await session.commit()
    await session.refresh(attachment)
    return attachment


async def cleanup_attachment_files(session: AsyncSession, transaction_ids: list[uuid.UUID]) -> None:
    """Delete storage files for all attachments belonging to the given transactions."""
    if not transaction_ids:
        return
    result = await session.execute(
        select(TransactionAttachment.storage_key).where(
            TransactionAttachment.transaction_id.in_(transaction_ids)
        )
    )
    storage_keys = [row[0] for row in result.all()]
    if not storage_keys:
        return
    storage = get_storage_provider()
    for key in storage_keys:
        try:
            await storage.delete(key)
        except Exception:
            pass  # best-effort cleanup; file may already be gone


async def delete_attachment(
    session: AsyncSession, attachment_id: uuid.UUID, workspace_id: uuid.UUID
) -> None:
    result = await session.execute(
        select(TransactionAttachment).where(
            TransactionAttachment.id == attachment_id,
            TransactionAttachment.workspace_id == workspace_id,
        )
    )
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise LookupError("Attachment not found")

    storage = get_storage_provider()
    await storage.delete(attachment.storage_key)

    await session.delete(attachment)
    await session.commit()
