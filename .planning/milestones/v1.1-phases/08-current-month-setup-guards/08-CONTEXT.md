# Phase 8: Current Month Setup & Guards - Context

**Gathered:** 2026-04-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Introduce the first-class `Mês Atual` monthly context and the guardrails that block sync and manual financial entry until that context is defined, while preserving the existing Flux layout language across affected screens.

</domain>

<decisions>
## Implementation Decisions

### Current Month Source of Truth
- Represent the active editable month as a single explicit month/year competency that the app can read consistently across dashboard, transactions, accounts, and categories flows.
- Treat the absence of that competency as a locked current-month state rather than silently defaulting to the calendar month.
- Keep the initial period-selection model simple in this phase: users define `Mês Atual`, and historical snapshots remain read-only context until later phases add full browsing and closure behavior.

### Guarded Mutations
- Block external sync when `Mês Atual` has no defined period.
- Block manual creation of transactions, accounts, and categories when `Mês Atual` has no defined period.
- Keep read access available so users can still inspect the app shell and any already closed history without mutating current-month state.

### UX and Layout Consistency
- Reuse the existing page-header, card, dialog, and inline status patterns already used by dashboard, accounts, transactions, and categories screens.
- Surface the missing-period state close to the current month selector and affected actions instead of inventing a separate setup-only screen.
- Keep mobile support intact by following the current responsive layout structure and button density.

### the agent's Discretion
- Exact persistence location for the current-month period state.
- Whether action blocking is enforced via disabled CTA states, server-side validation, or both, as long as the user-facing behavior is explicit and reliable.
- The smallest viable shared frontend abstraction for month-state reuse.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/src/pages/dashboard.tsx` already centralizes the active month selector and is the strongest candidate for the first visible `Mês Atual` control.
- `frontend/src/components/page-header.tsx`, `Button`, dialog primitives, and card patterns already define the layout language this phase must preserve.
- `frontend/src/lib/api.ts` is the central place to add any current-month status and mutation guard APIs.
- `backend/app/api/transactions.py`, `backend/app/api/accounts.py`, `backend/app/api/categories.py`, and `backend/app/api/connections.py` are the supported mutation/sync entry points that need shared guard behavior.

### Established Patterns
- Frontend data is driven through TanStack Query with page-local state and invalidation of broad domain keys after mutations.
- Backend validation failures are raised as `ValueError` in services and translated to `400` responses at router boundaries.
- Dashboard and readers currently infer the active month from date-based params, so this phase should introduce a shared month context before later snapshot phases build on it.

### Integration Points
- Dashboard month selection currently lives in `frontend/src/pages/dashboard.tsx`, while transactions/accounts/categories trigger creation or sync from their own pages and dialogs.
- Account creation, transaction creation, category creation, and bank sync each have separate frontend mutations and backend service entry points, so the guard needs a reusable rule instead of page-specific duplication only.
- Existing transaction and dashboard queries already use month/date filters; phase 8 should avoid rewriting historical data and instead establish the active-period gate that later phases can thread through the data model.

</code_context>

<specifics>
## Specific Ideas

- Add a visible current-month status card or inline banner in the existing dashboard header area instead of a brand-new navigation surface.
- Prefer a shared backend capability that answers "is current month defined?" so the frontend can both render state and align mutation blocking with server enforcement.
- Treat this phase as the guard/setup foundation only; full snapshot navigation belongs to later phases.

</specifics>

<deferred>
## Deferred Ideas

- Full closed-snapshot switcher UX and month-closing workflow.
- Period-linked transaction persistence and complete category-group schema cleanup, which are covered by later phases.

</deferred>
