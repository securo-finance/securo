# Phase 12 Context

## Why This Phase Exists

Milestone `v1.1` passed phase-local verification but failed the milestone audit due to cross-phase integration gaps:

- closed snapshots were not browseable when `Mês Atual` was undefined
- protected-history mode still had writable mutation paths through bulk categorization and import
- recurring transaction creation remained available without `Mês Atual`
- dashboard month-selection UX still used inconsistent controls and copy

## Product Direction Confirmed

- Categories remain available even when `Mês Atual` is undefined.
- Snapshot mode remains read-only for edit-oriented actions.
- Financial mutations still require an editable `Mês Atual`.

## Affected Surfaces

- `frontend/src/pages/dashboard.tsx`
- `frontend/src/pages/transactions.tsx`
- `frontend/src/pages/import.tsx`
- `frontend/src/pages/recurring.tsx`
- `frontend/src/pages/categories.tsx`
- `frontend/src/components/ui/date-picker-input.tsx`
- `backend/app/api/transactions.py`
- `backend/app/api/import_transactions.py`
- `backend/app/api/recurring_transactions.py`
- `backend/app/api/categories.py`
- `backend/app/services/month_service.py`
