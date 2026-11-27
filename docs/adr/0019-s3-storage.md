# 0019. Files can live in an S3-compatible bucket, from the regular image

- Status: Accepted
- Date: 2026-11-17
- Origin: #1075 (S3-compatible attachment storage)
- Supersedes: [0013](0013-file-storage.md)
- Extended by [0036](0036-xml-attachments.md): XML files are accepted, and anything that isn't an image or a PDF is served as a download.

## Context

ADR 0013 put every file behind `StorageProvider` and left S3 declared but not implemented. Installs on Kubernetes, or on hosts without a durable volume, want attachments in a bucket they already run (AWS S3, Garage, MinIO and the like) instead of on a local disk. Adding it must keep the promises of ADR 0004 and ADR 0016: off by default, nothing paid when off, and no outbound call the person didn't configure.

## Decision

- Services read and write files only through `StorageProvider` (`upload`, `download`, `delete`) in `backend/app/providers/storage.py`, obtained from `get_storage_provider()` in `backend/app/providers/__init__.py`. This is unchanged from 0013.
- `STORAGE_PROVIDER` picks the implementation, and it is `local` by default:
  - `local`: `LocalStorageProvider` (`backend/app/providers/local_storage.py`), which resolves every key and refuses one outside its base directory. In Compose it writes to the `attachments` volume at `/app/data/attachments`.
  - `s3`: `S3StorageProvider` (`backend/app/providers/s3_storage.py`), on `aiobotocore`, configured by the `STORAGE_S3_*` settings in `backend/app/core/config.py`. The settings are checked at startup only when S3 is selected.
- Keys are the same for both providers and start with the workspace: `{workspace_id}/{transaction_id}/...` for transactions, `{workspace_id}/invoices/{invoice_id}/...` for invoices. The S3 provider refuses an invalid key before any request. Moving from local to S3 is a copy of the attachments directory into the bucket, with no migration.
- Limits stay as in 0013: 10 MB per file; images and PDF only; 10 files per transaction, 20 per invoice. The accepted types are extended by [0036](0036-xml-attachments.md), which adds XML and is the current list.
- The S3 endpoint is an outbound call the person configures, and it extends the list in ADR 0016:

| Call | Where | Happens only when |
|---|---|---|
| S3-compatible object storage | `providers/s3_storage.py` | `STORAGE_PROVIDER=s3`; to the configured bucket and endpoint (AWS when no endpoint is set) |

  The client ignores endpoints from the SDK's own environment and config files, so only `STORAGE_S3_ENDPOINT_URL` decides where files go. `http://` endpoints are refused unless `STORAGE_S3_ALLOW_INSECURE_HTTP` is on. Nothing else is sent anywhere: no telemetry (ADR 0016).
- One backend image. `aiobotocore` is a regular dependency in `backend/pyproject.toml`, and `get_storage_provider()` imports `s3_storage` only in the `s3` branch, so an install on local storage never loads the S3 client.

## Consequences

- New file features use the provider and a workspace-prefixed key; no service opens paths on disk or calls S3 directly.
- The image carries the S3 client for everyone. That is about 50 MB installed, 31 MB of it `botocore` and 8.4 MB `aiohttp`, in exchange for one image to build, publish and document, and no special tag to switch to S3.
- Every backend, worker and MCP process must get the same storage settings, or they read and write in different places.
- AI knowledge documents and embedding caches stay on local storage; this decision covers attachments, invoice attachments and logos.

## Enforced by

`tests/test_local_storage.py` (path traversal), `tests/test_s3_storage.py` (keys, settings, endpoint handling), `tests/test_providers.py` (provider selection, and local storage never loading the S3 client), `tests/test_attachment_service.py`, `tests/test_attachments_api.py`, `tests/test_invoice_attachments.py`. `tests/test_s3_storage_integration.py` runs against a real bucket when its `SECURO_S3_TEST_*` settings are present.
