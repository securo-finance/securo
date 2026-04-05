# Phase 1: Connected Accounts & Model Simplification - Context

**Gathered:** 2026-04-04
**Status:** Ready for planning
**Mode:** Autonomous smart discuss

<domain>
## Phase Boundary

Users should only manage Pluggy-connected accounts from `Contas`. Manual accounts, wallets, and balance-centric account management are no longer part of the supported runtime.

</domain>

<decisions>
## Implementation Decisions

### Connected accounts become the only supported account surface
`/api/accounts` and the `Contas` page should expose only connected accounts. Manual account creation and wallet bootstrap are removed from supported flows.

### Renames must survive provider refreshes
Connected-account renames should persist independently from the provider’s raw account name, so sync updates do not overwrite user labels.

### Bill import toggle is per connected account
Each connected account gets its own `bill_import_enabled` flag. This flag will be used by later bill-sync phases.

### Connected account deletion should be soft
Deleting a connected account from Flux should hide it from the supported runtime without losing the connection linkage needed to prevent unwanted recreation on future syncs.

</decisions>

<code_context>
## Existing Code Insights

- `backend/app/services/account_service.py` still mixes manual accounts and bank-connected accounts in the same list/read/update/delete paths.
- `backend/app/services/connection_service.py` overwrites connected account names on sync, so a persistent custom-name field is needed.
- `backend/app/core/auth.py` and `backend/app/api/setup.py` still create a default wallet-like account during onboarding/setup.
- `frontend/src/pages/accounts.tsx` currently renders separate manual-account and bank-connection sections and displays per-account balances throughout the page.
- `frontend/src/components/app-layout.tsx` also surfaces account balances in the sidebar.

</code_context>

<specifics>
## Specific Ideas

- Add `custom_name` and `bill_import_enabled` on `accounts`.
- Update account APIs to treat connected accounts as the supported surface.
- Remove manual-account creation UI and wallet references from supported onboarding/account management.
- Replace balance-heavy account UI with management controls: rename, bill-import toggle, delete/close.

</specifics>

<deferred>
## Deferred Ideas

- The bill retrieval implementation itself belongs to Phase 2.
- Daily persistence and categorization behavior belong to Phase 3.
- Dashboard/month-summary removal of balance-based calculations belongs to Phase 4.

</deferred>
