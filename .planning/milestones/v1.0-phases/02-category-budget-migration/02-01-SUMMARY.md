---
phase: 02-category-budget-migration
plan: 01
subsystem: database
tags:
  - migration
  - categories
  - budgets
  - fastapi
  - alembic
provides:
  - Category-owned current budget state via `has_budget` and `budget_amount`
  - Flattened compatibility budget service with one row per category
affects:
  - phase-03-unified-category-budget-editing
  - phase-04-flat-categories-and-reader-cutover
tech-stack:
  added: []
  patterns:
    - Transitional dual-surface compatibility with categories as source of truth
key-files:
  created:
    - .planning/phases/02-category-budget-migration/02-CONTEXT.md
    - .planning/phases/02-category-budget-migration/02-01-PLAN.md
    - backend/alembic/versions/020_category_owned_budgets.py
  modified:
    - backend/app/models/category.py
    - backend/app/schemas/category.py
    - backend/app/services/category_service.py
    - backend/app/services/budget_service.py
    - frontend/src/types/index.ts
    - backend/tests/test_category_service.py
    - backend/tests/test_budget_service.py
    - backend/tests/test_budgets_api.py
key-decisions:
  - Flatten recurring and month-specific budget history into one current category-owned budget state
  - Preserve category groups temporarily while moving budget ownership to categories
patterns-established:
  - Category fields, not standalone budget rows, are now the source of truth for current budget state
  - Legacy budget endpoints remain as a compatibility facade until later phases remove them
requirements-completed:
  - CAT-04
  - MIG-01
duration: "55min"
completed: 2026-04-03
---

# Phase 2: Flatten Budgets Into Category-Owned State Summary

**Categories now own the current budget state directly, legacy budget history is flattened to one active budget row per category, and the compatibility budget API keeps the app working against the simplified model**

## Performance

- **Duration:** 55 min
- **Tasks:** 3
- **Files modified:** 11

## Accomplishments

- Added `has_budget` and `budget_amount` to the category model and exposed them through backend and frontend contracts.
- Created an Alembic migration that backfills category-owned budget state from legacy budget rows and collapses budget history to one surviving row per category.
- Rewrote the budget service and targeted tests around the simplified one-budget-per-category model while preserving compatibility endpoints.

## Task Commits

1. **Task 1: Add category-owned budget fields and migration backfill** - `not committed`
2. **Task 2: Simplify budget service semantics while preserving compatibility routes** - `not committed`
3. **Task 3: Refresh regression coverage for simplified migration behavior** - `not committed`

## Files Created/Modified

- `.planning/phases/02-category-budget-migration/02-CONTEXT.md` - Records the simplified migration decision and phase scope.
- `.planning/phases/02-category-budget-migration/02-01-PLAN.md` - Defines the migration and compatibility work for the phase.
- `backend/alembic/versions/020_category_owned_budgets.py` - Adds category-owned budget fields, backfills them, and collapses legacy budget rows.
- `backend/app/models/category.py` - Stores explicit category-owned budget state.
- `backend/app/schemas/category.py` - Exposes category budget state through the API contract.
- `backend/app/services/category_service.py` - Normalizes explicit budgeted versus unbudgeted category updates.
- `backend/app/services/budget_service.py` - Treats category budget fields as source of truth and flattens compatibility behavior.
- `frontend/src/types/index.ts` - Mirrors the new category budget fields for downstream UI work.
- `backend/tests/test_category_service.py` - Covers category budget-state normalization.
- `backend/tests/test_budget_service.py` - Covers flattened budget service behavior.
- `backend/tests/test_budgets_api.py` - Covers compatibility API behavior against the simplified model.

## Decisions & Deviations

The user explicitly chose to simplify budgets because the application is still in development, so recurring and month-specific budget semantics were intentionally removed rather than preserved. Category groups were deliberately left in place for now so transaction and rule category links remain stable while later phases replace grouped readers and editors.

## Next Phase Readiness

Phase 3 can now build a unified category create/edit flow on top of category-owned `has_budget` and `budget_amount` fields without depending on standalone budget ownership.
