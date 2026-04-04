# Phase 11: Snapshot Navigation & Protected History - Context

**Gathered:** 2026-04-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Let users switch the app’s main financial context between the editable `Mês Atual` and previously closed monthly snapshots, making closed history obviously read-only and protected.

</domain>

<decisions>
## Implementation Decisions

### View Context Model
- Keep `Mês Atual` as the only editable month.
- Let closed snapshots be selected as a separate read-only viewing context without rewriting the editable current-month preference.
- Reuse the persisted month/snapshot contract from Phase 10 instead of inventing a parallel history selector state per page.

### Protected History Behavior
- When a closed snapshot is selected, the app should make the read-only state obvious across affected pages.
- Manual creation and other financial mutations should require the user to return to `Mês Atual` before proceeding.
- Entering a closed snapshot view should be an explicit confirmation step so the user understands they are moving into preserved history.

### UX Scope
- The month context switcher belongs in the shared dashboard month-status surface and drives the app-wide default context.
- Accounts, transactions, and categories should reflect the selected snapshot context clearly enough that users do not mistake history for the editable month.

### the agent's Discretion
- Whether snapshot selection is stored as an optional selected snapshot period or another lightweight preference key.
- The exact balance between banners, badges, and disabled controls used to communicate read-only history.
- Which mutation endpoints need hard backend protection in addition to UI blocking, as long as closed-history review remains trustworthy.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/src/hooks/use-current-month.ts` already provides a shared month-state hook used by dashboard, accounts, transactions, and categories.
- `backend/app/services/account_service.py` and `backend/app/services/transaction_service.py` already default reads to the current month when no explicit date range is provided.
- `frontend/src/components/page-header.tsx` plus existing inline warning cards provide the right visual language for read-only history notices.

### Established Patterns
- App-wide read state is driven through TanStack Query and lightweight preferences-backed backend contracts.
- Read APIs commonly fall back to implicit current context when request parameters are omitted.
- Action buttons are already conditionally disabled when `Mês Atual` is undefined, so the same pattern can extend to read-only snapshot history.

### Integration Points
- The selected snapshot context needs to influence dashboard, transactions, and account reads without breaking explicit date filtering.
- Snapshot-view guards should align frontend disabled states with backend validation so protected history stays reliable.
- The month selector and snapshot metadata introduced in Phase 10 become the source of truth for app-wide context switching here.

</code_context>

<specifics>
## Specific Ideas

- Extend the months contract with `selected_mode`, `selected_period`, and a snapshots list.
- Add a dashboard selector that can switch between `Mês Atual` and closed snapshots after an explicit confirmation step.
- Show a closed-history banner on transactions/accounts/categories and reject mutations server-side while snapshot mode is active.

</specifics>

<deferred>
## Deferred Ideas

- Cross-snapshot comparison tools or diff views.
- Snapshot export/versioning features beyond protected read-only navigation.

</deferred>
