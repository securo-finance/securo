# Phase 2: Category Budget Migration - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Migrate the data model so categories own budget state directly, while keeping existing category, transaction, and rule links intact. The migration should flatten legacy recurring and month-specific budget history into one current budget state per category because the application is still in development and the user wants simpler budget behavior going forward.

</domain>

<decisions>
## Implementation Decisions

### Budget Ownership
- Add category-owned budget fields to the category model and expose them through category APIs for downstream phases.
- Treat category-owned budget state as the source of truth for the current effective budget.
- Keep legacy budget endpoints and tables temporarily as a compatibility layer until later cleanup phases.
- When legacy budget APIs are used during the transition, synchronize them back to category-owned budget fields so the migrated state does not drift.

### Budget Simplification
- Flatten existing recurring and month-specific budget history into a single current budget value per category.
- Choose the latest existing budget record per category as the migrated value; older recurring history does not need to be preserved as active behavior.
- Remove reliance on `is_recurring` and month-specific override semantics in budget service behavior going forward.
- Accept that budget comparison now uses the category-owned current budget state rather than historical month-aware budget schedules.

### Category Relationships
- Do not remove category IDs or change transaction/rule category references in this phase.
- Do not remove category groups yet; preserve `group_id` so existing grouped readers and screens keep working until later phases.
- Ensure users with no budgets continue to have categories with an explicit unbudgeted state.

### the agent's Discretion
- The exact backfill rule for selecting the surviving legacy budget row may be based on latest month, then most recent creation time as a tie-breaker.
- Transitional compatibility code may keep legacy budget responses stable as long as the simplified category-owned model remains authoritative.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/app/models/category.py`, `backend/app/schemas/category.py`, and `backend/app/services/category_service.py` already define the central category contract and are the right place to add category-owned budget fields.
- `backend/app/models/budget.py` and `backend/app/services/budget_service.py` contain the current recurring/override logic that must be flattened.
- `frontend/src/types/index.ts` already mirrors category and budget response shapes for the SPA.

### Established Patterns
- Backend schema changes land through Alembic migrations under `backend/alembic/versions/`.
- FastAPI routes stay thin and delegate state changes to services.
- Frontend API calls already treat categories and budgets as separate resources, so the migration can preserve temporary compatibility while the source of truth moves.

### Integration Points
- `backend/app/api/categories.py` and `backend/app/api/budgets.py` expose the contracts Phase 3 and Phase 4 will build on.
- `backend/app/services/budget_service.py` feeds both the budgets page and budget-vs-actual reader behavior.
- `backend/app/api/export.py` serializes categories and budgets for backups, so new category-owned fields will naturally flow into exports once added to the model.

</code_context>

<specifics>
## Specific Ideas

The user explicitly chose to simplify budgets because the application is still in development. Favor a clean current-state model over preserving time-aware budget history.

</specifics>

<deferred>
## Deferred Ideas

- Remove category groups and grouped UI after the migration has stabilized.
- Remove the standalone budgets CRUD surface after category create/edit becomes the primary editing flow.
- Introduce any explicit backup/export compatibility versioning during the reader cutover phase.

</deferred>
