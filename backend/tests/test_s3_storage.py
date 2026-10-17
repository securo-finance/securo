from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.s3_storage import S3StorageProvider


@pytest.fixture
def s3_settings(monkeypatch):
    # Explicitly isolate these tests from deployment configuration.
    import os

    for key in os.environ:
        if key.startswith("STORAGE_"):
            monkeypatch.delenv(key)
    return Settings(
        _env_file=None,
        _secrets_dir=None,
        storage_provider="s3",
        storage_s3_bucket="attachments",
        storage_s3_region="garage",
        storage_s3_endpoint_url="https://s3.example.com",
        storage_s3_addressing_style="path",
        storage_s3_access_key="test-access",
        storage_s3_secret_key="test-secret",
    )


@pytest.fixture
def s3(s3_settings):
    client = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = client
    session = MagicMock()
    session.create_client.return_value = context
    with (
        patch("app.providers.s3_storage.get_settings", return_value=s3_settings),
        patch("app.providers.s3_storage.get_session", return_value=session),
    ):
        yield S3StorageProvider(), client, session, context


@pytest.mark.asyncio
async def test_upload_preserves_key_bytes_and_content_type(s3):
    provider, client, session, context = s3
    key = "workspace/invoices/invoice/receipt.pdf"
    payload = b"%PDF-1.4\x00\xff"
    stored = await provider.upload(key, payload, "application/pdf")
    client.put_object.assert_awaited_once_with(
        Bucket="attachments", Key=key, Body=payload, ContentType="application/pdf",
    )
    assert (stored.storage_key, stored.size, stored.content_type) == (
        key, len(payload), "application/pdf",
    )
    options = session.create_client.call_args.kwargs
    assert options["endpoint_url"] == "https://s3.example.com"
    assert options["region_name"] == "garage"
    assert options["aws_access_key_id"] == "test-access"
    assert options["aws_secret_access_key"] == "test-secret"
    config = options["config"]
    assert config.s3 == {"addressing_style": "path"}
    assert config.signature_version == "s3v4"
    assert config.connect_timeout == 5
    assert config.read_timeout == 30
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}
    assert config.request_checksum_calculation == "when_required"
    context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_aws_default_endpoint_and_credential_chain(s3, s3_settings):
    provider, _, session, _ = s3
    s3_settings.storage_s3_endpoint_url = ""
    s3_settings.storage_s3_region = ""
    s3_settings.storage_s3_addressing_style = "auto"
    from pydantic import SecretStr

    s3_settings.storage_s3_access_key = SecretStr("")
    s3_settings.storage_s3_secret_key = SecretStr("")
    await provider.delete("workspace/missing.pdf")
    options = session.create_client.call_args.kwargs
    for key in ("endpoint_url", "region_name", "aws_access_key_id", "aws_secret_access_key", "aws_session_token"):
        assert options[key] is None
    assert options["config"].s3 == {"addressing_style": "auto"}


@pytest.mark.asyncio
async def test_temporary_credentials(s3, s3_settings):
    from pydantic import SecretStr

    provider, _, session, _ = s3
    s3_settings.storage_s3_session_token = SecretStr("test-token")
    await provider.delete("workspace/missing.pdf")
    assert session.create_client.call_args.kwargs["aws_session_token"] == "test-token"


@pytest.mark.asyncio
async def test_download_closes_body_and_client(s3):
    provider, client, _, context = s3
    body = AsyncMock()
    body.__aenter__.return_value = body
    body.read.return_value = b"receipt data"
    client.get_object.return_value = {"Body": body}
    assert await provider.download("workspace/receipt.pdf") == b"receipt data"
    client.get_object.assert_awaited_once_with(Bucket="attachments", Key="workspace/receipt.pdf")
    body.__aexit__.assert_awaited_once()
    context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_read_closes_body_and_client(s3):
    provider, client, _, context = s3
    body = AsyncMock()
    body.__aenter__.return_value = body
    body.read.side_effect = TimeoutError("read timed out")
    client.get_object.return_value = {"Body": body}
    with pytest.raises(TimeoutError):
        await provider.download("workspace/receipt.pdf")
    body.__aexit__.assert_awaited_once()
    context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_object_matches_local_provider(s3):
    provider, client, _, context = s3
    client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "GetObject",
    )
    with pytest.raises(FileNotFoundError, match="workspace/missing.pdf"):
        await provider.download("workspace/missing.pdf")
    context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["AccessDenied", "NoSuchBucket", "SlowDown"])
async def test_storage_failure_is_not_reported_as_missing_file(s3, code):
    provider, client, _, context = s3
    client.get_object.side_effect = ClientError({"Error": {"Code": code}}, "GetObject")
    with pytest.raises(ClientError):
        await provider.download("workspace/receipt.pdf")
    context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["upload", "download", "delete"])
async def test_network_failure_propagates(s3, operation):
    provider, client, _, context = s3
    error = EndpointConnectionError(endpoint_url="https://s3.example.com")
    for method in (client.put_object, client.get_object, client.delete_object):
        method.side_effect = error
    with pytest.raises(EndpointConnectionError):
        if operation == "upload":
            await provider.upload("workspace/file.pdf", b"data", "application/pdf")
        else:
            await getattr(provider, operation)("workspace/file.pdf")
    context.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_does_not_require_object_to_exist(s3):
    provider, client, _, _ = s3
    await provider.delete("workspace/missing.pdf")
    client.delete_object.assert_awaited_once_with(Bucket="attachments", Key="workspace/missing.pdf")
    client.head_object.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "/abs", "../escape", "a/../b", "a/./b", "a//b", "a/", "a\\b", "a\x00b", "a\nb"])
@pytest.mark.parametrize("operation", ["upload", "download", "delete"])
async def test_invalid_keys_never_reach_s3(s3, key, operation):
    provider, _, session, _ = s3
    with pytest.raises(ValueError, match="Invalid storage key"):
        if operation == "upload":
            await provider.upload(key, b"data", "text/plain")
        else:
            await getattr(provider, operation)(key)
    session.create_client.assert_not_called()


@pytest.mark.parametrize("overrides", [
    {"storage_s3_bucket": " "},
    {"storage_s3_access_key": ""},
    {"storage_s3_secret_key": ""},
    {"storage_s3_access_key": "", "storage_s3_secret_key": "", "storage_s3_session_token": "token"},
    {"storage_provider": "typo"},
    {"storage_s3_addressing_style": "typo"},
    {"storage_s3_endpoint_url": "file:///tmp/s3"},
    {"storage_s3_endpoint_url": "https://user:secret@s3.example.com"},
    {"storage_s3_endpoint_url": "https://s3.example.com?secret=value"},
])
def test_invalid_configuration_fails_early(s3_settings, overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, _secrets_dir=None, **(s3_settings.model_dump() | overrides))


def test_local_default_does_not_require_s3_configuration(s3_settings):
    settings = Settings(_env_file=None, _secrets_dir=None)
    assert settings.storage_provider == "local"
    assert settings.storage_s3_bucket == ""
