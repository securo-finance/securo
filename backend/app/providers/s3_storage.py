"""S3-compatible storage for attachments and invoice logos."""

from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.providers.storage import StorageProvider, StoredFile


def _validate_key(storage_key: str) -> None:
    # Keep keys portable between local storage and S3. Do not normalize a key:
    # silently rewriting it would make the object disagree with the DB record.
    if (
        not storage_key
        or "\\" in storage_key
        or any(part in {"", ".", ".."} for part in storage_key.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in storage_key)
    ):
        raise ValueError("Invalid storage key")


class S3StorageProvider(StorageProvider):
    @property
    def name(self) -> str:
        return "s3"

    def _client(self):
        settings = get_settings()
        # A client per operation avoids sharing connections across the API's
        # event loop and Celery's short-lived asyncio.run() loops. Both client
        # and response body are closed deterministically, including on failure.
        return get_session().create_client(
            "s3",
            endpoint_url=settings.storage_s3_endpoint_url or None,
            region_name=settings.storage_s3_region or None,
            aws_access_key_id=settings.storage_s3_access_key.get_secret_value() or None,
            aws_secret_access_key=settings.storage_s3_secret_key.get_secret_value() or None,
            aws_session_token=settings.storage_s3_session_token.get_secret_value() or None,
            config=AioConfig(
                signature_version="s3v4",
                s3={"addressing_style": settings.storage_s3_addressing_style},
                connect_timeout=5,
                read_timeout=30,
                retries={"mode": "standard", "total_max_attempts": 3},
                # Optional SDK checksum trailers are not supported by every
                # S3-compatible server. Retain checksums when the API requires them.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    async def upload(self, storage_key: str, data: bytes, content_type: str) -> StoredFile:
        _validate_key(storage_key)
        async with self._client() as client:
            await client.put_object(
                Bucket=get_settings().storage_s3_bucket,
                Key=storage_key,
                Body=data,
                ContentType=content_type,
            )
        return StoredFile(storage_key=storage_key, size=len(data), content_type=content_type)

    async def download(self, storage_key: str) -> bytes:
        _validate_key(storage_key)
        async with self._client() as client:
            try:
                response = await client.get_object(
                    Bucket=get_settings().storage_s3_bucket, Key=storage_key,
                )
            except ClientError as exc:
                # Do not disguise missing buckets, denied access or outages as
                # missing files. Callers must be able to distinguish those failures.
                if exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                    raise FileNotFoundError(f"File not found: {storage_key}") from exc
                raise
            async with response["Body"] as stream:
                return await stream.read()

    async def delete(self, storage_key: str) -> None:
        _validate_key(storage_key)
        async with self._client() as client:
            # S3 DeleteObject is idempotent for missing keys; other failures
            # propagate so callers don't treat an unsuccessful delete as success.
            await client.delete_object(Bucket=get_settings().storage_s3_bucket, Key=storage_key)
