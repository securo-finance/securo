from unittest.mock import AsyncMock, patch
from dataclasses import dataclass

import pytest
from httpx import AsyncClient

from app.models.transaction import Transaction
from app.services.attachment_service import sanitize_filename


@dataclass
class FakeStoredFile:
    storage_key: str
    size: int
    content_type: str


def _make_mock_storage():
    """Create a mock storage provider for tests."""
    mock = AsyncMock()
    mock.name = "mock"
    mock.upload.side_effect = lambda key, data, ct: FakeStoredFile(
        storage_key=key, size=len(data), content_type=ct
    )
    mock.download.return_value = b"fake-file-content"
    mock.delete.return_value = None
    return mock


STORAGE_PATCH = "app.services.attachment_service.get_storage_provider"

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ── Filename sanitization ──────────────────────────────────


def test_sanitize_filename_basic():
    assert sanitize_filename("receipt.png") == "receipt.png"


def test_sanitize_filename_special_chars():
    assert sanitize_filename("my file (1).png") == "my_file_1.png"


def test_sanitize_filename_unicode():
    assert sanitize_filename("café résumé.pdf") == "caf_r_sum.pdf"


def test_sanitize_filename_collapse_underscores():
    assert sanitize_filename("a---b___c   d.jpg") == "a---b_c_d.jpg"


def test_sanitize_filename_no_extension():
    assert sanitize_filename("README") == "README"


def test_sanitize_filename_empty_name():
    assert sanitize_filename("....pdf") == "file.pdf"


# ── Upload ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_attachment(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    with patch(STORAGE_PATCH, return_value=_make_mock_storage()):
        response = await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("receipt.png", TINY_PNG, "image/png")},
        )
    assert response.status_code == 201
    data = response.json()
    assert data["filename"] == "receipt.png"
    assert data["content_type"] == "image/png"
    assert data["size"] == len(TINY_PNG)
    assert data["transaction_id"] == str(txn.id)


@pytest.mark.asyncio
async def test_upload_sanitizes_filename(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    with patch(STORAGE_PATCH, return_value=_make_mock_storage()):
        response = await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("my file (1).png", TINY_PNG, "image/png")},
        )
    assert response.status_code == 201
    assert response.json()["filename"] == "my_file_1.png"


@pytest.mark.asyncio
async def test_upload_pdf(client: AsyncClient, auth_headers, test_transactions: list[Transaction]):
    txn = test_transactions[0]
    with patch(STORAGE_PATCH, return_value=_make_mock_storage()):
        response = await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
    assert response.status_code == 201
    assert response.json()["filename"] == "invoice.pdf"


@pytest.mark.asyncio
async def test_upload_rejected_file_type(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    with patch(STORAGE_PATCH, return_value=_make_mock_storage()):
        response = await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("malware.exe", b"MZ...", "application/octet-stream")},
        )
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_file_too_large(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    huge = b"x" * (11 * 1024 * 1024)  # 11 MB > 10 MB limit
    with patch(STORAGE_PATCH, return_value=_make_mock_storage()):
        response = await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("big.png", huge, "image/png")},
        )
    assert response.status_code == 400
    assert "too large" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_transaction_not_found(client: AsyncClient, auth_headers, test_transactions):
    fake_id = "00000000-0000-0000-0000-000000000000"
    with patch(STORAGE_PATCH, return_value=_make_mock_storage()):
        response = await client.post(
            f"/api/transactions/{fake_id}/attachments",
            headers=auth_headers,
            files={"file": ("receipt.png", TINY_PNG, "image/png")},
        )
    assert response.status_code == 404


# ── List ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_attachments_empty(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    response = await client.get(f"/api/transactions/{txn.id}/attachments", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_attachments(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    mock_storage = _make_mock_storage()
    with patch(STORAGE_PATCH, return_value=mock_storage):
        # Upload two files
        await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("a.png", TINY_PNG, "image/png")},
        )
        await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("b.jpg", TINY_PNG, "image/jpeg")},
        )

    response = await client.get(f"/api/transactions/{txn.id}/attachments", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    filenames = {a["filename"] for a in data}
    assert filenames == {"a.png", "b.jpg"}


@pytest.mark.asyncio
async def test_list_attachments_transaction_not_found(
    client: AsyncClient, auth_headers, test_transactions
):
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/transactions/{fake_id}/attachments", headers=auth_headers)
    assert response.status_code == 404


# ── Download ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_attachment(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    mock_storage = _make_mock_storage()
    with patch(STORAGE_PATCH, return_value=mock_storage):
        upload_resp = await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("photo.png", TINY_PNG, "image/png")},
        )
        attachment_id = upload_resp.json()["id"]

        response = await client.get(
            f"/api/transactions/{txn.id}/attachments/{attachment_id}",
            headers=auth_headers,
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "photo.png" in response.headers.get("content-disposition", "")
    assert response.content == b"fake-file-content"


@pytest.mark.asyncio
async def test_download_attachment_not_found(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    fake_id = "00000000-0000-0000-0000-000000000000"
    with patch(STORAGE_PATCH, return_value=_make_mock_storage()):
        response = await client.get(
            f"/api/transactions/{txn.id}/attachments/{fake_id}",
            headers=auth_headers,
        )
    assert response.status_code == 404


# ── Delete ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_attachment(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    mock_storage = _make_mock_storage()
    with patch(STORAGE_PATCH, return_value=mock_storage):
        upload_resp = await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("delete-me.png", TINY_PNG, "image/png")},
        )
        attachment_id = upload_resp.json()["id"]

        # Delete
        response = await client.delete(
            f"/api/transactions/{txn.id}/attachments/{attachment_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        # Verify gone
        list_resp = await client.get(
            f"/api/transactions/{txn.id}/attachments", headers=auth_headers
        )
        assert all(a["id"] != attachment_id for a in list_resp.json())

    # Verify storage.delete was called
    mock_storage.delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_attachment_not_found(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    fake_id = "00000000-0000-0000-0000-000000000000"
    with patch(STORAGE_PATCH, return_value=_make_mock_storage()):
        response = await client.delete(
            f"/api/transactions/{txn.id}/attachments/{fake_id}",
            headers=auth_headers,
        )
    assert response.status_code == 404


# ── Auth ────────────────────────────────────────────────────


# ── Rename ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rename_attachment(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    mock_storage = _make_mock_storage()
    with patch(STORAGE_PATCH, return_value=mock_storage):
        upload_resp = await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("receipt.png", TINY_PNG, "image/png")},
        )
        attachment_id = upload_resp.json()["id"]

    response = await client.patch(
        f"/api/transactions/{txn.id}/attachments/{attachment_id}",
        headers=auth_headers,
        json={"filename": "grocery store receipt"},
    )
    assert response.status_code == 200
    data = response.json()
    # Should sanitize and preserve original extension
    assert data["filename"] == "grocery_store_receipt.png"


@pytest.mark.asyncio
async def test_rename_preserves_extension(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    mock_storage = _make_mock_storage()
    with patch(STORAGE_PATCH, return_value=mock_storage):
        upload_resp = await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        attachment_id = upload_resp.json()["id"]

    # Try renaming with wrong extension — should keep .pdf
    response = await client.patch(
        f"/api/transactions/{txn.id}/attachments/{attachment_id}",
        headers=auth_headers,
        json={"filename": "my-invoice.jpg"},
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "my-invoice.pdf"


@pytest.mark.asyncio
async def test_rename_attachment_not_found(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.patch(
        f"/api/transactions/{txn.id}/attachments/{fake_id}",
        headers=auth_headers,
        json={"filename": "new-name"},
    )
    assert response.status_code == 404


# ── Settings ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_attachment_settings(client: AsyncClient):
    response = await client.get("/api/settings/attachments")
    assert response.status_code == 200
    data = response.json()
    assert data["allowed_extensions"] == ["jpg", "jpeg", "png", "webp", "gif", "heic", "pdf"]
    assert data["max_file_size_mb"] == 10
    assert data["max_attachments_per_transaction"] == 10


# ── Max attachments ────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_rejected_max_attachments(
    client: AsyncClient, auth_headers, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    mock_storage = _make_mock_storage()
    with (
        patch(STORAGE_PATCH, return_value=mock_storage),
        patch("app.services.attachment_service.get_settings") as mock_settings,
    ):
        settings = mock_settings.return_value
        settings.storage_max_file_size_mb = 10
        settings.storage_allowed_extensions = "jpg,jpeg,png,webp,gif,heic,pdf"
        settings.storage_max_attachments_per_transaction = 2

        # Upload two files (at the limit)
        for i in range(2):
            resp = await client.post(
                f"/api/transactions/{txn.id}/attachments",
                headers=auth_headers,
                files={"file": (f"file{i}.png", TINY_PNG, "image/png")},
            )
            assert resp.status_code == 201

        # Third upload should be rejected
        response = await client.post(
            f"/api/transactions/{txn.id}/attachments",
            headers=auth_headers,
            files={"file": ("file3.png", TINY_PNG, "image/png")},
        )
    assert response.status_code == 400
    assert "maximum" in response.json()["detail"].lower()


# ── Auth ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_attachments_unauthenticated(
    client: AsyncClient, test_transactions: list[Transaction]
):
    txn = test_transactions[0]
    response = await client.get(f"/api/transactions/{txn.id}/attachments")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_upload_unauthenticated(client: AsyncClient, test_transactions: list[Transaction]):
    txn = test_transactions[0]
    response = await client.post(
        f"/api/transactions/{txn.id}/attachments",
        files={"file": ("receipt.png", TINY_PNG, "image/png")},
    )
    assert response.status_code == 401
