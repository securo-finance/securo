# Phase 3: Unified Category Budget Editing - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Replace the split category and budget editing experience with one responsive category form that can create or edit a category and its optional budget state in the same flow. Keep the existing design language, keep group assignment optional, and avoid desktop-only interactions.

</domain>

<decisions>
## Implementation Decisions

### Single Write Flow
- The category dialog becomes the primary write surface for both category fields and budget settings.
- Users can leave the group empty; category creation must not require a category group.
- Budget enablement is an explicit toggle, not an implied zero-value amount.
- The budgets page remains available as a reader surface but no longer acts as the primary editor.

### Mobile Behavior
- Keep the current dialog pattern but make the category form scrollable and usable within mobile-height viewports.
- Use tap-friendly controls only; no hover-only disclosure or drag interactions.
- Keep the budget toggle and amount input in the same form section so the budget state is visible at a glance.
- Preserve the existing shell tokens and layout language instead of redesigning the page.

### the agent's Discretion
- Supporting copy can steer users from the budgets page back to categories as long as the budgets route still works during transition.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/src/pages/categories.tsx` already owns category create/edit dialogs and can absorb budget controls directly.
- `frontend/src/pages/budgets.tsx` already reads budget data and can be downgraded to a read-only summary surface.
- `frontend/src/components/ui/dialog.tsx`, `button.tsx`, and existing input primitives are already mobile-safe enough for the form change.

### Established Patterns
- Translation strings live in `frontend/src/locales/*.json`.
- API contract coverage for categories lives in `backend/tests/test_categories_api.py`.
- React query invalidation already refreshes categories and groups after category updates.

### Integration Points
- Category budget values travel through `frontend/src/lib/api.ts` and the category schema/type contract.
- The budgets page reads compatibility budget rows, so redirecting editing responsibility there still relies on Phase 2 compatibility behavior.

</code_context>

<specifics>
## Specific Ideas

The user wants to proceed quickly. Favor a practical, cleaner single form over preserving the old separate budget editor as a parallel write path.

</specifics>

<deferred>
## Deferred Ideas

- Full flat-category UI removal of group sections belongs to Phase 4.
- Complete retirement of the budgets route belongs to later cleanup after all readers are migrated.

</deferred>
