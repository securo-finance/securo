# Phase 4: Flat Categories and Reader Cutover - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Finish the transition away from grouped category reading by making category management visibly flat, removing group data from budget comparison readers, and making backup exports declare the v2 compatibility story explicitly.

</domain>

<decisions>
## Implementation Decisions

### Flat Management
- Categories should render as one flat list on mobile and desktop.
- Group metadata may survive temporarily in stored data, but the main management view should no longer depend on grouped sections or collapse state.

### Reader Cutover
- Budget comparison should expose category-owned budget state only.
- Readers should stop leaking group identifiers once they no longer contribute to the supported workflow.

### Export Compatibility
- Backups should move to a `2.0` format version.
- Legacy `category_groups` data can remain in the archive during transition, but metadata must explain that category-owned budgets are now the source of truth.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/src/pages/categories.tsx` already contains the category list and can be flattened without redesigning the dialog flow.
- `backend/app/services/budget_service.py` already reads budget state from categories, so the remaining cutover is contract cleanup.
- `backend/app/api/export.py` already centralizes backup metadata and can carry the compatibility story.

### Integration Points
- `frontend/src/types/index.ts` and `backend/app/schemas/budget.py` define the budget comparison contract.
- `backend/tests/test_export_api.py` covers backup contents and versioning.
- `backend/tests/test_budgets_api.py` covers the comparison endpoint used by downstream readers.

</code_context>

<deferred>
## Deferred Ideas

- Full schema removal of `category_groups` belongs to the final cleanup phase, after active consumers are gone.
- Public route removal for legacy category-group and standalone budget editing belongs to Phase 5.

</deferred>
