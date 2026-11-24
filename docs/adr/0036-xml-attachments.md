# 0036. XML files can be attached, and anything that isn't an image or a PDF is served as a download

- Status: Accepted
- Date: 2026-11-23
- Origin: fiscal and bank documents that come as XML
- Amends: [0019](0019-s3-storage.md)

## Context

ADR 0019 kept attachments to images and PDF. Many documents people keep next to a transaction or an invoice come as XML: electronic invoices, bank statements, receipts a service exports. The file type is the easy part. The risk is how the file comes back: an XML document opened in the browser can carry XHTML with script, and served inline on the app's origin it would run with the person's session.

## Decision

- `xml` joins the default `STORAGE_ALLOWED_EXTENSIONS` (`backend/app/core/config.py`), for transaction and invoice attachments alike. An install that set its own list keeps it.
- Files are still served by their extension, never the type the upload claimed (`served_content_type`). XML has no served type of its own, so it goes out as raw bytes (octet-stream).
- `served_headers` in `backend/app/services/attachment_service.py` decides how a file is handed over: inline for the images and PDF the app previews, `attachment` for everything else. Every attachment response also sends `X-Content-Type-Options: nosniff`, so the browser never guesses a renderable type for the bytes.
- The transaction preview pane says there is no preview for such a file and keeps its download button; the invoice document view already says so.

## Consequences

- An XML file is stored and downloaded like any other attachment, under the same size and count limits.
- No XML (nor any later non-previewable type) is ever rendered on the app's origin, whatever it contains.

## Enforced by

`backend/tests/test_attachments_api.py` and `backend/tests/test_invoice_attachments.py`: an XML file carrying a script is accepted and comes back as raw bytes (octet-stream), as an attachment and with nosniff; an image stays inline. `frontend/src/components/transaction-attachments.test.tsx`: the file picker offers XML when the server allows it.
