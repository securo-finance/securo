# 0037. Documents are read into transactions by an LLM, reviewed before anything is saved

- Status: Accepted
- Date: 2026-11-23
- Origin: #592 (statements that banks only give as PDF), receipts kept as photos
- Extends: [0014](0014-ai-agents-module.md), [0016](0016-outbound-calls-and-no-telemetry.md)

## Context

Many banks hand out a statement or a card bill only as a PDF, and receipts live as photos. The import reads OFX, CSV, QIF and CAMT; a PDF used to fall through to the QIF parser and come back as an empty preview with no error. A parser per bank layout needs real samples of every layout and breaks when a bank changes its PDF. A language model reads any layout, but it is a cost, a dependency and, unless it runs locally, a place the document is sent to.

## Decision

- **Part of the agents module.** Document reading is available while `AGENTS_ENABLED` is on (ADR 0014) and costs nothing otherwise: every `/api/document-ai/*` route answers 404, the worker task is never dispatched and no model is called (ADR 0004). It needs none of the agents' containers. Within a workspace it is on once an owner picks an LLM connection for it, and off until then.
- **Reading is a model call, the result is a proposal.** The provider layer of the agents module (`backend/app/agents/providers/`) is reused as a library; messages can now carry images (`ImagePart`). `backend/app/services/document_reader.py` reads a PDF from its own text when it has some, which any model can do and costs far less, and sends page images (rendered with pypdfium2, no system library) only for scans and photos. The model answers JSON, which is validated: dates, amounts as exact decimals, lines without a date or an amount dropped and counted.
- **Nothing lands without a person.** A statement's rows go into the regular import preview and are saved by `POST /api/transactions/import`, with its duplicate detection and rules, carrying `document_extraction_id`. A receipt is attached to a transaction it matches (same amount, a few days around its date) or creates one. A statement is checked against its own opening and closing balances, and the review says when the rows do not add up.
- **Does the model read images?** Answered before anything is sent (`backend/app/services/document_ai_capabilities.py`): the setting when someone set it, then Ollama's own `/api/show` capabilities, then the model's name against known families. Unknown means try; a provider that refuses the image is reported as the same "needs a model that reads images" error. A photo is refused at upload for a model known to read text only, and the UI says so ahead of time.
- **Which model: an agents connection, not a second configuration.** Providers, URLs, keys and default models are already managed as LLM connections under Agents; document reading reads with one of them instead of keeping its own. A workspace owner picks one of their own connections (`workspace_document_ai_settings.connection_id`), from the workspace settings or from the connection itself under Agents, which edit the same setting. An optional model replaces the connection's default for reading, and the vision setting corrects the detection. The workspace never holds a key: a connection deleted since, or one whose owner left the workspace, reads as off. The column has no foreign key on purpose: the agents models are imported only while that module is on.
- **What is kept.** `document_extractions` holds the file's storage key (under the workspace prefix, ADR 0019), what the model read, and, once confirmed, the rows as the person kept them, next to who confirmed and where they went. A layout fingerprint (issuer, kind, account digits, header words) lets the next statement of the same layout preselect the account it went to last time. Both tables are left out of backups: the transactions they produced are backed up, the reads are not.
- **Outbound call** (extends ADR 0016): the uploaded document goes to the provider of the workspace's chosen connection, only with `AGENTS_ENABLED`, a connection picked, and someone uploading a document; the row for LLM providers already covers it. With Ollama on the same machine it never leaves it.

## Consequences

- PDF and image files dropped on the import page get a clear error when the feature is off, instead of an empty preview.
- Reading takes from seconds to a minute; it runs on the Celery worker and the client polls. A read stuck for 15 minutes is marked failed and can be retried, for instance after choosing another model.
- Model output can be wrong. The balance check, the review table, duplicate detection and the confirmed rows kept next to the model's are the safeguards; nothing is auto-imported.
- The corrections people make are stored but not yet used beyond the account suggestion; learning more per layout builds on the same rows.

## Enforced by

`backend/tests/test_document_ai.py`: routes closed while the agents module is off; reading off until an owner picks one of their own connections, never someone else's; a deleted connection turns it off; the model and vision come from the connection unless overridden; a photo refused for a text-only model; a text PDF sent without images and a scanned one as images; a refused image read as `vision_required`; a statement imported end to end with its confirmation recorded and its account suggested next time; a receipt attached and one creating a transaction; another workspace's read is a 404; viewers cannot upload. `backend/tests/api_contract/` records every route and its statuses.
