# Phase 10: Month Closure & Next Month Start - Context

**Gathered:** 2026-04-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Turn the active `Mês Atual` into a preserved closed-month snapshot and immediately bootstrap the next editable month without breaking the existing Flux dashboard-first flow.

</domain>

<decisions>
## Implementation Decisions

### Snapshot Shape
- Represent a closed month as a dedicated snapshot record tied to the exact persisted `monthly_periods` row that is being closed.
- Keep snapshots immutable by treating closed periods as history records rather than reopening them as the editable `Mês Atual`.
- Prevent duplicate closures for the same monthly period.

### Month-Closure Flow
- The user closes the active month from the existing dashboard month-control surface.
- The close action must collect the next `Mês Atual` period in the same flow so the app never lands in an ambiguous post-close state.
- Successful closure clears any historical view selection and moves the editable context to the newly defined next month immediately.

### Scope Boundary
- This phase owns snapshot creation, period linkage validation, and next-month bootstrap.
- Rich snapshot navigation and cross-page closed-history UX are deferred to Phase 11, but Phase 10 should expose the backend contract those reads will rely on.

### the agent's Discretion
- Exact snapshot metadata stored beyond the linked monthly period and close timestamp.
- Whether the close flow is modeled as a dedicated endpoint or an extension of the month contract, as long as the API remains explicit and testable.
- The minimal dashboard UX needed to make month closure clear without introducing a new layout pattern.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/app/services/month_service.py` already owns current-month normalization and persisted monthly-period resolution.
- `frontend/src/pages/dashboard.tsx` already contains the current-month status surface and is the natural place for a `Fechar Mês` control.
- `frontend/src/hooks/use-current-month.ts` and `frontend/src/lib/api.ts` already centralize month-state reads for the app.

### Established Patterns
- Backend router endpoints translate `ValueError` into `400` responses for domain validation failures.
- Frontend month setup currently uses page-local dialog/form state plus TanStack Query invalidation of `current-month` and `dashboard`.
- Planning artifacts in this repo keep one primary execute plan per phase unless the scope truly needs multiple waves.

### Integration Points
- Closing the month must validate against the active editable period created in Phases 8-9, not against raw date windows.
- Snapshot creation should be available to Phase 11 reads without requiring data duplication or a later contract break.
- Dashboard copy and action patterns must stay inside the current Flux card/header language.

</code_context>

<specifics>
## Specific Ideas

- Add a `monthly_snapshots` table keyed by user and `monthly_period_id`.
- Expose a close-month API that returns updated month state plus the created snapshot metadata.
- Offer the next month input inline with the close action instead of sending the user through a separate wizard.

</specifics>

<deferred>
## Deferred Ideas

- Cross-page snapshot switching and protected-history banners.
- Explicit confirmation UX before entering or mutating closed-month control state, which belongs to Phase 11.

</deferred>
