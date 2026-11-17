# 0013. Files go through a storage provider

- Status: Superseded by 0019
- Date: 2026-03-27
- Origin: #37 (transaction attachments), #718 (invoice attachments)

## Context

Receipts, invoice attachments and logos are user files. Where they live should be a deployment choice, and a file name from a user must never become a path outside the storage area.

## Decision

- Services read and write files only through `StorageProvider` (`upload`, `download`, `delete`) in `backend/app/providers/storage.py`, obtained from `get_storage_provider()`.
- The one implementation is `LocalStorageProvider` (`backend/app/providers/local_storage.py`), which resolves every key and refuses one outside its base directory. In Compose it writes to the `attachments` volume at `/app/data/attachments`.
- Keys start with the workspace: `{workspace_id}/{transaction_id}/...` for transactions, `{workspace_id}/invoices/{invoice_id}/...` for invoices.
- Limits: 10 MB per file; images and PDF only; 10 files per transaction, 20 per invoice.

## Consequences

- New file features use the provider and a workspace-prefixed key; no service opens paths on disk.
- `STORAGE_PROVIDER=s3` and its `STORAGE_S3_*` settings are declared but not implemented; any value but `local` raises. Adding S3 is a new provider, nothing else changes.

## Enforced by

`tests/test_local_storage.py` (path traversal), `tests/test_attachment_service.py`, `tests/test_attachments_api.py`, `tests/test_invoice_attachments.py`.
