# Phase 3: Snapshots and Category Filters - Context

**Gathered:** 2026-04-05
**Status:** Ready for planning
**Mode:** Autonomous smart discuss

<domain>
## Phase Boundary

The selected card transaction workflow should remain useful when reviewing closed snapshots and when narrowing results by category.

</domain>

<decisions>
## Implementation Decisions

### Reuse the global month-view mechanism
Snapshot switching should use the existing `months.setView()` flow so card browsing stays aligned with the rest of the product's current-month and snapshot model.

### Keep category filtering local to the card screen
The card detail page should expose an explicit category filter while keeping the selected card fixed.

### Prefer snapshot-aware queries over custom date logic
The card detail view should rely on the current selected month/snapshot context rather than adding manual date filters that would bypass the monthly snapshot model.

</decisions>

<code_context>
## Existing Code Insights

- `frontend/src/pages/dashboard.tsx` already contains the snapshot-selection mutation and user messaging.
- `frontend/src/hooks/use-current-month.ts` exposes the current month and snapshot context for any page.
- `backend/app/services/transaction_service.py` already scopes transaction queries to the selected month/snapshot when no explicit date range is provided.

</code_context>

<specifics>
## Specific Ideas

- Add the existing month-view selector pattern to the card detail page.
- Add a category filter next to search.
- Make query invalidation refresh card transactions after snapshot switches.

</specifics>

<deferred>
## Deferred Ideas

- Multi-card comparison, totals, and invoice analytics remain future work.

</deferred>
