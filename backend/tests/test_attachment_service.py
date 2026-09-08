import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.providers.storage import StoredFile
from app.services.attachment_service import (
    _validate_file,
    cleanup_attachment_files,
    delete_attachment,
    download_attachment,
    list_attachments,
    rename_attachment,
    sanitize_filename,
    upload_attachment,
)


# ---------------------------------------------------------------------------
# sanitize_filename — pure function
# ---------------------------------------------------------------------------


def test_sanitize_basic():
    assert sanitize_filename("report.pdf") == "report.pdf"


def test_sanitize_special_chars():
    assert sanitize_filename("my file (1).pdf") == "my_file_1.pdf"


def test_sanitize_no_extension():
    assert sanitize_filename("readme") == "readme"


def test_sanitize_empty_name():
    assert sanitize_filename("...pdf") == "file.pdf"


def test_sanitize_unicode():
    result = sanitize_filename("café résumé.txt")
    assert result.endswith(".txt")


def test_sanitize_multiple_dots():
    result = sanitize_filename("file.backup.tar.gz")
    assert result.endswith(".gz")


def test_sanitize_collapses_underscores():
    assert sanitize_filename("a___b.pdf") == "a_b.pdf"


# ---------------------------------------------------------------------------
# _validate_file
# ---------------------------------------------------------------------------


def test_validate_file_too_large():
    with pytest.raises(ValueError, match="too large"):
        _validate_file("big.pdf", "application/pdf", 200 * 1024 * 1024)


def test_validate_file_bad_extension():
    with pytest.raises(ValueError, match="not allowed"):
        _validate_file("hack.exe", "application/octet-stream", 100)


def test_validate_file_ok():
    # Should not raise for allowed extensions
    _validate_file("receipt.pdf", "application/pdf", 1024)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def txn_account(session: AsyncSession, test_user):
    acct = Account(
        id=uuid.uuid4(), user_id=test_user.id, name="Attach Acct",
        type="checking", balance=Decimal("0"), currency="BRL",
    )
    session.add(acct)
    await session.commit()
    await session.refresh(acct)
    return acct


@pytest_asyncio.fixture
async def txn_for_attach(session: AsyncSession, test_user, txn_account):
    txn = Transaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=txn_account.id,
        description="Attach test", amount=Decimal("100"), date=datetime.now(timezone.utc).date(),
        type="debit", source="manual", currency="BRL",
        created_at=datetime.now(timezone.utc),
    )
    session.add(txn)
    await session.commit()
    await session.refresh(txn)
    return txn


# ---------------------------------------------------------------------------
# upload_attachment
# ---------------------------------------------------------------------------


async def test_upload_attachment(session: AsyncSession, test_user, test_workspace, txn_for_attach):
    mock_storage = AsyncMock()
    mock_storage.upload = AsyncMock(return_value=StoredFile(
        storage_key="test/key", size=100, content_type="application/pdf",
    ))
    with patch("app.services.attachment_service.get_storage_provider", return_value=mock_storage):
        att = await upload_attachment(
            session, test_workspace.id, test_user.id, txn_for_attach.id,
            "receipt.pdf", "application/pdf", b"fake-data",
        )
    assert att.filename == "receipt.pdf"
    assert att.transaction_id == txn_for_attach.id


async def test_upload_attachment_max_limit(session: AsyncSession, test_user, test_workspace, txn_for_attach):
    """Exceeding max attachments per transaction raises ValueError."""
    # Pre-seed attachments up to the limit
    from app.core.config import get_settings
    limit = get_settings().storage_max_attachments_per_transaction
    for i in range(limit):
        att = TransactionAttachment(
            id=uuid.uuid4(), user_id=test_user.id, transaction_id=txn_for_attach.id,
            filename=f"file{i}.pdf", storage_key=f"k{i}", content_type="application/pdf", size=10,
        )
        session.add(att)
    await session.commit()

    mock_storage = AsyncMock()
    mock_storage.upload = AsyncMock(return_value=StoredFile(
        storage_key="test/key", size=100, content_type="application/pdf",
    ))
    with patch("app.services.attachment_service.get_storage_provider", return_value=mock_storage):
        with pytest.raises(ValueError, match="Maximum"):
            await upload_attachment(
                session, test_workspace.id, test_user.id, txn_for_attach.id,
                "extra.pdf", "application/pdf", b"data",
            )


async def test_upload_wrong_transaction(session: AsyncSession, test_user, test_workspace, txn_for_attach):
    """Uploading to a non-existent transaction raises LookupError."""
    mock_storage = AsyncMock()
    with patch("app.services.attachment_service.get_storage_provider", return_value=mock_storage):
        with pytest.raises(LookupError, match="not found"):
            await upload_attachment(
                session, test_workspace.id, test_user.id, uuid.uuid4(),
                "file.pdf", "application/pdf", b"data",
            )


# ---------------------------------------------------------------------------
# list_attachments
# ---------------------------------------------------------------------------


async def test_list_attachments_empty(session: AsyncSession, test_user, test_workspace, txn_for_attach):
    result = await list_attachments(session, test_workspace.id, txn_for_attach.id)
    assert result == []


# ---------------------------------------------------------------------------
# download_attachment
# ---------------------------------------------------------------------------


async def test_download_attachment(session: AsyncSession, test_user, test_workspace, txn_for_attach):
    att = TransactionAttachment(
        id=uuid.uuid4(), user_id=test_user.id, transaction_id=txn_for_attach.id,
        filename="dl.pdf", storage_key="dl/key", content_type="application/pdf", size=5,
    )
    session.add(att)
    await session.commit()

    mock_storage = AsyncMock()
    mock_storage.download = AsyncMock(return_value=b"pdf-bytes")
    with patch("app.services.attachment_service.get_storage_provider", return_value=mock_storage):
        result_att, data = await download_attachment(session, att.id, test_workspace.id)
    assert data == b"pdf-bytes"
    assert result_att.filename == "dl.pdf"


async def test_download_attachment_not_found(session: AsyncSession, test_user, test_workspace):
    with pytest.raises(LookupError, match="not found"):
        await download_attachment(session, uuid.uuid4(), test_workspace.id)


# ---------------------------------------------------------------------------
# rename_attachment
# ---------------------------------------------------------------------------


async def test_rename_preserves_extension(session: AsyncSession, test_user, test_workspace, txn_for_attach):
    att = TransactionAttachment(
        id=uuid.uuid4(), user_id=test_user.id, transaction_id=txn_for_attach.id,
        filename="original.pdf", storage_key="ren/key", content_type="application/pdf", size=5,
    )
    session.add(att)
    await session.commit()

    result = await rename_attachment(session, att.id, test_workspace.id, "newname.txt")
    # Original extension .pdf should be preserved
    assert result.filename == "newname.pdf"


async def test_rename_same_extension(session: AsyncSession, test_user, test_workspace, txn_for_attach):
    att = TransactionAttachment(
        id=uuid.uuid4(), user_id=test_user.id, transaction_id=txn_for_attach.id,
        filename="doc.pdf", storage_key="ren2/key", content_type="application/pdf", size=5,
    )
    session.add(att)
    await session.commit()

    result = await rename_attachment(session, att.id, test_workspace.id, "renamed.pdf")
    assert result.filename == "renamed.pdf"


async def test_rename_not_found(session: AsyncSession, test_user, test_workspace):
    with pytest.raises(LookupError, match="not found"):
        await rename_attachment(session, uuid.uuid4(), test_workspace.id, "x.pdf")


# ---------------------------------------------------------------------------
# delete_attachment
# ---------------------------------------------------------------------------


async def test_delete_attachment(session: AsyncSession, test_user, test_workspace, txn_for_attach):
    att = TransactionAttachment(
        id=uuid.uuid4(), user_id=test_user.id, transaction_id=txn_for_attach.id,
        filename="del.pdf", storage_key="del/key", content_type="application/pdf", size=5,
    )
    session.add(att)
    await session.commit()

    mock_storage = AsyncMock()
    with patch("app.services.attachment_service.get_storage_provider", return_value=mock_storage):
        await delete_attachment(session, att.id, test_workspace.id)
    mock_storage.delete.assert_called_once_with("del/key")


async def test_delete_attachment_not_found(session: AsyncSession, test_user, test_workspace):
    mock_storage = AsyncMock()
    with patch("app.services.attachment_service.get_storage_provider", return_value=mock_storage):
        with pytest.raises(LookupError, match="not found"):
            await delete_attachment(session, uuid.uuid4(), test_workspace.id)


# ---------------------------------------------------------------------------
# cleanup_attachment_files
# ---------------------------------------------------------------------------


async def test_cleanup_empty_list(session: AsyncSession):
    await cleanup_attachment_files(session, [])  # no-op, should not raise


async def test_cleanup_deletes_storage_keys(session: AsyncSession, test_user, txn_for_attach):
    att1 = TransactionAttachment(
        id=uuid.uuid4(), user_id=test_user.id, transaction_id=txn_for_attach.id,
        filename="c1.pdf", storage_key="c1/key", content_type="application/pdf", size=5,
    )
    att2 = TransactionAttachment(
        id=uuid.uuid4(), user_id=test_user.id, transaction_id=txn_for_attach.id,
        filename="c2.pdf", storage_key="c2/key", content_type="application/pdf", size=5,
    )
    session.add_all([att1, att2])
    await session.commit()

    mock_storage = AsyncMock()
    with patch("app.services.attachment_service.get_storage_provider", return_value=mock_storage):
        await cleanup_attachment_files(session, [txn_for_attach.id])
    assert mock_storage.delete.call_count == 2


async def test_cleanup_ignores_storage_errors(session: AsyncSession, test_user, txn_for_attach):
    att = TransactionAttachment(
        id=uuid.uuid4(), user_id=test_user.id, transaction_id=txn_for_attach.id,
        filename="err.pdf", storage_key="err/key", content_type="application/pdf", size=5,
    )
    session.add(att)
    await session.commit()

    mock_storage = AsyncMock()
    mock_storage.delete = AsyncMock(side_effect=Exception("storage down"))
    with patch("app.services.attachment_service.get_storage_provider", return_value=mock_storage):
        # Should not raise
        await cleanup_attachment_files(session, [txn_for_attach.id])
