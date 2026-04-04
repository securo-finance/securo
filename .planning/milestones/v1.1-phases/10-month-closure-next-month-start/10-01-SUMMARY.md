---
phase: 10-month-closure-next-month-start
plan: 01
subsystem: month-lifecycle
tags:
  - monthly-snapshots
  - month-closure
  - dashboard
provides:
  - Persisted `monthly_snapshots` records linked to the exact closed `monthly_periods` row
  - `/api/months/close` workflow that closes the active month and opens the next editable month immediately
  - Dashboard month-control UI for `Fechar Mês` with inline next-month capture
affects: []
tech-stack:
  added:
    - SQLAlchemy model for `monthly_snapshots`
    - Alembic migration `022`
  patterns:
    - Month-state responses now return snapshot metadata alongside editable-month state
key-files:
  created:
    - backend/app/models/monthly_snapshot.py
    - backend/alembic/versions/022_monthly_snapshots.py
    - .planning/phases/10-month-closure-next-month-start/10-01-SUMMARY.md
    - .planning/phases/10-month-closure-next-month-start/10-VERIFICATION.md
  modified:
    - backend/app/api/months.py
    - backend/app/models/__init__.py
    - backend/app/schemas/month.py
    - backend/app/services/month_service.py
    - backend/tests/conftest.py
    - backend/tests/test_months_api.py
    - frontend/src/lib/api.ts
    - frontend/src/types/index.ts
    - frontend/src/pages/dashboard.tsx
    - frontend/src/locales/en.json
    - frontend/src/locales/pt-BR.json
key-decisions:
  - Snapshots are first-class records keyed by user and period instead of implicit preference history
  - Closing a month clears any historical selection and opens the next editable month in the same mutation
  - The dashboard remains the single place where month lifecycle actions are initiated
patterns-established:
  - Month lifecycle writes now return a full month-state payload so the frontend can refresh without bespoke follow-up requests
requirements-completed:
  - SNAP-01
  - PERIOD-04
  - DATA-03
duration: "90min"
completed: 2026-04-04
---

# Phase 10: Month Closure & Next Month Start Summary

**Flux can now close the active `Mês Atual` into a preserved snapshot and roll directly into the next editable month.**

## Accomplishments

- Added persisted `monthly_snapshots` plus service helpers that validate the exact active monthly period before closure and reject duplicate closures.
- Introduced `/api/months/close`, returning both the created snapshot and the refreshed month-state contract with the new editable month already active.
- Expanded shared month-state types so the frontend receives snapshot metadata together with current-month state.
- Added dashboard controls for `Fechar Mês`, including the required next-month input in the same inline flow.

## Completion Note

Phase 10 completes the core month-lifecycle transition. Closed months are now durable historical objects, and the app immediately lands in a valid next `Mês Atual` instead of an undefined post-close state.
