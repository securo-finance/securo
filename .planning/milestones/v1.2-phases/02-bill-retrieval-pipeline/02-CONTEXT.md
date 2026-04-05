# Phase 2: Bill Retrieval Pipeline - Context

**Gathered:** 2026-04-05
**Status:** Executed autonomously
**Mode:** Autonomous smart discuss

<domain>
## Phase Boundary

Pluggy sync should stop treating all connected-account transactions as the supported runtime. For this milestone, Flux only imports credit-card bill transactions relevant to the active month workflow.

</domain>

<decisions>
## Implementation Decisions

### Bill retrieval follows Pluggy's current bills API
The official Pluggy bills flow is modeled around `GET /bills` filtered by `accountId`. The milestone wording was updated to match the current provider documentation instead of preserving an outdated nested route assumption.

### Bill selection is due-date driven
For `Mês Atual`, Flux selects bills whose `dueDate` falls in the month immediately after the active period, then imports only transactions linked to those bill IDs.

### General transaction sync is disabled in supported credit-card flows
Connected checking/savings data is no longer imported through the supported sync path for this milestone. Credit-card imports are filtered down to bill-linked transactions only.

</decisions>

<code_context>
## Existing Code Insights

- `backend/app/providers/pluggy.py` already exposed transaction import but not a bill abstraction.
- Pluggy transactions expose `billId` only after the bill exists at the institution.
- `backend/app/services/connection_service.py` was importing all transactions for all connected accounts.

</code_context>

<specifics>
## Specific Ideas

- Add provider-level `BillData`.
- Fetch bills per account, choose the target due month, then filter transactions by `billId`.
- Keep payee extraction and FX stamping behavior intact for the filtered transactions.

</specifics>
