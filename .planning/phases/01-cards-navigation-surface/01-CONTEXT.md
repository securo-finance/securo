# Phase 1: Cards Navigation Surface - Context

**Gathered:** 2026-04-05
**Status:** Ready for planning
**Mode:** Autonomous smart discuss

<domain>
## Phase Boundary

Users should be able to discover imported Pluggy credit cards from a dedicated `Cartões` area in the shell navigation.

</domain>

<decisions>
## Implementation Decisions

### Reuse connected-account data instead of adding a new card model
Imported cards already exist as connected accounts with `type = credit_card`. The new navigation surface should consume that existing account data.

### Keep `Cartões` as a first-class route
The new menu entry should live directly below `Transações` and route to a dedicated cards listing page.

### List only imported credit-card accounts
The cards page should filter the existing connected-account list down to credit cards so the milestone stays aligned with Pluggy-imported card browsing.

</decisions>

<code_context>
## Existing Code Insights

- `frontend/src/components/app-layout.tsx` owns the shell navigation structure.
- `frontend/src/App.tsx` owns protected routes.
- `frontend/src/lib/api.ts` already exposes `accounts.list()` and `accounts.get()`.
- `backend/app/services/account_service.py` already returns only connected accounts, so the cards surface can derive from current API responses.

</code_context>

<specifics>
## Specific Ideas

- Add `Cartões` to nav translations and sidebar config.
- Create `/cards` route and page.
- Render credit-card accounts as clickable cards that lead to a detail workflow in later phases.

</specifics>

<deferred>
## Deferred Ideas

- Card-scoped transaction browsing belongs to Phase 2.
- Snapshot switching and category filtering in the card detail flow belong to Phase 3.

</deferred>
