import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.providers.local_storage import LocalStorageProvider
from app.providers.storage import StoredFile


# ---------------------------------------------------------------------------
# StoredFile dataclass
# ---------------------------------------------------------------------------


def test_stored_file_creation():
    sf = StoredFile(storage_key="user/file.pdf", size=1024, content_type="application/pdf")
    assert sf.storage_key == "user/file.pdf"
    assert sf.size == 1024
    assert sf.content_type == "application/pdf"


# ---------------------------------------------------------------------------
# StorageProvider interface
# ---------------------------------------------------------------------------


def test_storage_provider_get_url_default():
    """Base get_url returns None."""
    # Can't instantiate ABC directly, use LocalStorageProvider
    provider = LocalStorageProvider()
    assert provider.get_url("any/key") is None


def test_storage_provider_name():
    provider = LocalStorageProvider()
    assert provider.name == "local"


# ---------------------------------------------------------------------------
# LocalStorageProvider tests
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def local_storage(storage_dir):
    with patch("app.providers.local_storage.get_settings") as mock_settings:
        settings = mock_settings.return_value
        settings.storage_local_path = storage_dir
        provider = LocalStorageProvider()
        yield provider


@pytest.mark.asyncio
async def test_upload_creates_file(local_storage, storage_dir):
    result = await local_storage.upload("test/file.txt", b"hello world", "text/plain")
    assert result.storage_key == "test/file.txt"
    assert result.size == 11
    assert result.content_type == "text/plain"
    # File should exist on disk
    assert (Path(storage_dir) / "test" / "file.txt").exists()


@pytest.mark.asyncio
async def test_upload_creates_parent_dirs(local_storage, storage_dir):
    await local_storage.upload("a/b/c/deep.txt", b"data", "text/plain")
    assert (Path(storage_dir) / "a" / "b" / "c" / "deep.txt").exists()


@pytest.mark.asyncio
async def test_download_reads_file(local_storage, storage_dir):
    await local_storage.upload("dl/test.txt", b"content", "text/plain")
    data = await local_storage.download("dl/test.txt")
    assert data == b"content"


@pytest.mark.asyncio
async def test_download_file_not_found(local_storage):
    with pytest.raises(FileNotFoundError):
        await local_storage.download("nonexistent/file.txt")


@pytest.mark.asyncio
async def test_delete_removes_file(local_storage, storage_dir):
    await local_storage.upload("del/test.txt", b"data", "text/plain")
    assert (Path(storage_dir) / "del" / "test.txt").exists()
    await local_storage.delete("del/test.txt")
    assert not (Path(storage_dir) / "del" / "test.txt").exists()


@pytest.mark.asyncio
async def test_delete_nonexistent_no_error(local_storage):
    """Deleting a file that doesn't exist should not raise."""
    await local_storage.delete("ghost/file.txt")


@pytest.mark.asyncio
async def test_path_traversal_blocked(local_storage):
    with pytest.raises(ValueError, match="Invalid storage key"):
        await local_storage.upload("../../etc/passwd", b"evil", "text/plain")


@pytest.mark.asyncio
async def test_path_traversal_download_blocked(local_storage):
    with pytest.raises(ValueError, match="Invalid storage key"):
        await local_storage.download("../../etc/passwd")


@pytest.mark.asyncio
async def test_path_traversal_delete_blocked(local_storage):
    with pytest.raises(ValueError, match="Invalid storage key"):
        await local_storage.delete("../../etc/passwd")


@pytest.mark.asyncio
async def test_path_traversal_prefix_collision_blocked(local_storage, storage_dir):
    # Test sibling folder sharing the same prefix name
    sibling_evil = f"../{Path(storage_dir).name}_evil/stolen.txt"
    with pytest.raises(ValueError, match="Invalid storage key"):
        await local_storage.upload(sibling_evil, b"evil", "text/plain")

