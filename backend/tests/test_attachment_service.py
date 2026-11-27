import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.services.attachment_service import cleanup_attachment_files, sanitize_filename


def test_sanitize_multiple_dots():
    assert sanitize_filename("file.backup.tar.gz") == "file_backup_tar.gz"


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
# cleanup_attachment_files
# ---------------------------------------------------------------------------


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
    deleted = {call.args[0] for call in mock_storage.delete.await_args_list}
    assert deleted == {"c1/key", "c2/key"}


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
