# Phase 2: Card Transaction Browsing - Context

**Gathered:** 2026-04-05
**Status:** Ready for planning
**Mode:** Autonomous smart discuss

<domain>
## Phase Boundary

Users should be able to open one imported card and inspect only that card's transactions in a workflow that feels like the existing `Transações` screen.

</domain>

<decisions>
## Implementation Decisions

### Keep the selected card bound to the existing account-backed transaction model
The detail view should use the selected card's account ID as a fixed transaction filter instead of introducing card-specific transaction persistence.

### Preserve familiar transaction interactions where compatible
Search, export, pagination, row inspection, and current-month edit restrictions should remain aligned with the existing transaction experience.

### Avoid global `Transações` regressions
The existing transactions page should keep its current behavior. Card browsing should be introduced as a separate route.

</decisions>

<code_context>
## Existing Code Insights

- `frontend/src/pages/transactions.tsx` already implements the transaction browser, dialog, bulk categorize bar, and export flow.
- `backend/app/api/transactions.py` and `backend/app/services/transaction_service.py` already support `account_id` filtering.
- `frontend/src/pages/account-detail.tsx` is currently a redirect stub, so card detail needs a real page.

</code_context>

<specifics>
## Specific Ideas

- Build a card detail page at `/cards/:id`.
- Query the selected account plus account-scoped transactions.
- Keep familiar table and dialog behavior for that card's imported transactions.

</specifics>

<deferred>
## Deferred Ideas

- Snapshot switching and explicit category filtering controls for the card view belong to Phase 3.

</deferred>
