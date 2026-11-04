import os
from pathlib import Path

import aiofiles

from app.core.config import get_settings
from app.providers.storage import StorageProvider, StoredFile


class LocalStorageProvider(StorageProvider):
    """Store files on the local filesystem."""

    @property
    def name(self) -> str:
        return "local"

    def _base_path(self) -> Path:
        return Path(get_settings().storage_local_path)

    def _full_path(self, storage_key: str) -> Path:
        base = self._base_path().resolve()
        full = (base / storage_key).resolve()
        try:
            full.relative_to(base)
        except ValueError:
            raise ValueError("Invalid storage key")
        return full

    async def upload(self, storage_key: str, data: bytes, content_type: str) -> StoredFile:
        path = self._full_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        return StoredFile(storage_key=storage_key, size=len(data), content_type=content_type)

    async def download(self, storage_key: str) -> bytes:
        path = self._full_path(storage_key)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {storage_key}")
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def delete(self, storage_key: str) -> None:
        path = self._full_path(storage_key)
        if path.exists():
            os.remove(path)
