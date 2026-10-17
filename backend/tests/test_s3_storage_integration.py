"""Opt-in real S3 round trip; use a dedicated, pre-created test bucket.

Only SECURO_S3_TEST_* variables are used, never application storage credentials.
See the README's Attachment Storage section for invocation.
"""

import os
import uuid
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.providers.s3_storage import S3StorageProvider


@pytest.mark.asyncio
async def test_s3_server_round_trip():
    names = ("ENDPOINT", "REGION", "BUCKET", "ACCESS_KEY", "SECRET_KEY")
    values = {name: os.environ.get(f"SECURO_S3_TEST_{name}", "") for name in names}
    if not all(values.values()):
        pytest.skip("Set SECURO_S3_TEST_* variables to test against a dedicated S3 bucket")
    settings = Settings(
        _env_file=None,
        _secrets_dir=None,
        storage_provider="s3",
        storage_s3_bucket=values["BUCKET"],
        storage_s3_region=values["REGION"],
        storage_s3_endpoint_url=values["ENDPOINT"],
        storage_s3_addressing_style="path",
        storage_s3_access_key=values["ACCESS_KEY"],
        storage_s3_secret_key=values["SECRET_KEY"],
        storage_s3_session_token=os.environ.get("SECURO_S3_TEST_SESSION_TOKEN", ""),
    )
    # Different service paths all share this provider; spaces and unicode also
    # catch incorrect URL/key handling in S3-compatible implementations.
    prefix = f"securo-integration/{uuid.uuid4().hex}"
    keys = [f"{prefix}/{suffix}" for suffix in (
        "transaction/receipt café.pdf", "invoices/invoice/receipt.pdf", "invoices/logo/logo.png",
    )]
    payload = b"%PDF-1.4\x00\xff\n" * 1024
    with patch("app.providers.s3_storage.get_settings", return_value=settings):
        provider = S3StorageProvider()
        try:
            for key in keys:
                stored = await provider.upload(key, payload, "application/octet-stream")
                assert stored.storage_key == key
                assert stored.size == len(payload)
                assert await provider.download(key) == payload
                async with provider._client() as client:
                    metadata = await client.head_object(Bucket=values["BUCKET"], Key=key)
                    assert metadata["ContentType"] == "application/octet-stream"
                    assert metadata["ContentLength"] == len(payload)
                await provider.delete(key)
                await provider.delete(key)
                with pytest.raises(FileNotFoundError):
                    await provider.download(key)
        finally:
            for key in keys:
                await provider.delete(key)
