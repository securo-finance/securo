# 0058. Plugin hosted sign-in, incremental cursor feeds, and synced loan terms

- Status: Accepted
- Date: 2026-11-27
- Origin: #1058 (Plaid API support via provider plugin)
- Extends: [0015](0015-plugins.md), [0023](0023-new-bank-providers-are-plugins.md)

## Context

[ADR 0023](0023-new-bank-providers-are-plugins.md) moves new bank sync providers to plugins while keeping the shared pipeline and sign-in primitives in the core. Aggregators such as Plaid (and OAuth2 PKCE or hosted-link providers generally) hit three generic gaps in the v1 plugin interface:

1. **Stateful redirect sign-in without `?code=`**: Plaid Hosted Link creates a short-lived `link_token` during `oauth_url`, redirects the user back to a registered completion URI without an authorization `code` (and possibly without echoing a custom `state` query parameter), and resolves the completed session server-side via `/link/token/get` using that `link_token`. Previously `authorize_url` discarded any `secrets` or `state` returned by `oauth_url`, and the callback required `?code=...&state=...`.
2. **Cursor-based incremental feeds**: APIs such as Plaid `/transactions/sync` deliver deltas (`added`, `modified`, `removed`) from a cursor rather than re-sending a full date window. They link a newly posted entry to the exact pending entry it settled (`pending_transaction_id`) even when the amount (e.g. a tip) or date changed, and name explicitly removed entries rather than relying on absence from a complete window. Without knowing a feed is incremental, the pipeline's phantom-duplicate cleanup treats older siblings absent from the delta as missing from the bank.
3. **Synced loan contract metadata**: Securo's `accounts` table already stores loan terms (`loan_kind`, `loan_original_amount`, `loan_interest_rate`, `loan_rate_period`, `loan_term_months`, `loan_first_payment_date`, `loan_amortization`), but `AccountData` and `contract.Account` could not carry them from a provider.

## Decision

- **Stateful plugin redirect sign-in** (`backend/app/plugins/connections.py`, `backend/app/api/plugins.py`, `backend/app/schemas/plugin.py`, `frontend/src/lib/plugin-oauth.ts`, `frontend/src/components/plugin-connections.tsx`, `frontend/src/pages/plugin-oauth-callback.tsx`):
  - `oauth_url` receives `redirect_uri`, `state`, and `connection_state`, and may return `secrets` and/or `state` alongside its `https://` `url`. Securo persists them before redirecting so `oauth_exchange` can read ephemeral handshake values (such as a link token or PKCE verifier).
  - Returning `None` for a key in `secrets` removes that key from the sealed store without counting as a newly stored credential; `signed_in` stays true only when at least one non-null plugin secret is stored.
  - `POST /api/plugins/connections` and `POST /api/plugins/connections/{id}/authorize` include the single-use `oauth_state` token in their JSON reply. The frontend stores it in `sessionStorage` immediately before redirecting and clears it on callback, allowing services that omit `state` on their completion redirect to complete the single-use Redis state check safely.
  - `POST /api/plugins/oauth/callback` accepts an optional `code` and a bounded `params` map (at most 32 keys), forwarding both plus `connection_state` to `oauth_exchange`.
- **Backward-compatible Contract v1 extensions** (`backend/app/providers/base.py`, `backend/app/plugins/contract.py`, `backend/app/plugins/provider.py`, `backend/app/services/connection_service.py`):
  - `Transaction` / `TransactionData` add optional `replaces_external_id` (posted transactions only, never self-referential) and `card_masked_number`. When present, `sync_connection` and `_run_first_import` upgrade that exact pending synced row in place before fuzzy matching or twin heuristics, updating settled amount/date/payee while preserving user edits, categories, and links.
  - `Account` adds optional `incremental` (boolean, default `false`) and `removed_transaction_ids` (unique, disjoint from `transactions`, capped at `MAX_REMOVED_TRANSACTIONS`, requiring `transactions` in `provides`). `BankProvider.get_removed_transaction_ids` exposes explicit removals; `_delete_removed_transactions` runs after the upsert loop and deletes only untouched synced rows using the same user-work and link guards as `_expire_cancelled_holds`. Incremental accounts advance `last_sync_at` normally while skipping window-based hold expiry and phantom-duplicate cleanup.
  - `Account` / `AccountData` add optional `available_credit` and loan terms (`loan_kind`, `loan_original_amount`, `loan_interest_rate`, `loan_rate_period`, `loan_term_months`, `loan_first_payment_date`, `loan_amortization`), and `connection_service` populates them (plus `payment_due_day` and `minimum_payment`) on `loan` accounts while preserving stored values when omitted on later syncs.
- **Reference Plaid provider plugin**: `examples/plugins/plaid/` demonstrates these capabilities with Python's standard library only (Hosted Link, Update Mode, `/transactions/sync`, `/liabilities/get`, `/investments/*`).

## Consequences

- Existing OAuth2 and provider plugins remain 100% compatible under `CONTRACT_VERSION = 1`.
- Providers with hosted sign-in flows, PKCE, cursor-based transaction sync, or loan liabilities need no provider-specific code or frontend SDK in Securo core.
- No database migration or new API route is required.

## Enforced by

`backend/tests/test_code_plugins.py` (stateful `oauth_url`, codeless callback with `params`, `oauth_state` API handoff, secret deletion), `backend/tests/test_plugin_contract.py` (contract validation for `replaces_external_id`, `removed_transaction_ids`, `incremental`, `available_credit`, `card_masked_number`, and loan terms), `backend/tests/test_provider_plugins.py` (replacement settlement, safe explicit deletion, incremental phantom-cleanup guard, loan term sync), `backend/tests/test_plaid_provider_plugin.py` (reference Plaid plugin end-to-end), `frontend/src/pages/plugin-oauth-callback.test.tsx`, and `frontend/src/components/plugin-code.test.tsx`.
