---
phase: 11-snapshot-navigation-protected-history
plan: 01
subsystem: snapshot-navigation
tags:
  - snapshot-view
  - read-only-history
  - guards
provides:
  - App-wide selected snapshot context layered on top of the current-month preference
  - Backend read routing and mutation guards that respect preserved history mode
  - Dashboard, accounts, transactions, and categories UI that clearly signals closed-history review
affects: []
tech-stack:
  added: []
  patterns:
    - Protected-history state is preference-backed and surfaced through the shared month-state hook
key-files:
  created:
    - .planning/phases/11-snapshot-navigation-protected-history/11-01-SUMMARY.md
    - .planning/phases/11-snapshot-navigation-protected-history/11-VERIFICATION.md
  modified:
    - backend/app/api/accounts.py
    - backend/app/api/categories.py
    - backend/app/api/connections.py
    - backend/app/api/transactions.py
    - backend/app/schemas/month.py
    - backend/app/services/account_service.py
    - backend/app/services/dashboard_service.py
    - backend/app/services/month_service.py
    - backend/app/services/transaction_service.py
    - backend/tests/test_accounts_api.py
    - backend/tests/test_dashboard_api.py
    - backend/tests/test_months_api.py
    - backend/tests/test_transactions_api.py
    - frontend/src/lib/api.ts
    - frontend/src/types/index.ts
    - frontend/src/pages/dashboard.tsx
    - frontend/src/pages/accounts.tsx
    - frontend/src/pages/transactions.tsx
    - frontend/src/pages/categories.tsx
    - frontend/src/locales/en.json
    - frontend/src/locales/pt-BR.json
key-decisions:
  - Closed-history selection is stored separately from the editable current-month preference
  - Implicit reads follow the selected snapshot context when one is active
  - Entering snapshot view requires explicit confirmation and disables edit-oriented controls across key pages
patterns-established:
  - Shared month-state UX now controls both editable lifecycle actions and protected-history navigation
requirements-completed:
  - PERIOD-02
  - SNAP-02
  - SNAP-03
  - GUARD-01
duration: "95min"
completed: 2026-04-04
---

# Phase 11: Snapshot Navigation & Protected History Summary

**Flux now lets users browse closed monthly snapshots as an explicit read-only context without confusing them with the editable `Mês Atual`.**

## Accomplishments

- Added selected snapshot state to the shared month contract so the app can distinguish editable-month state from preserved-history state.
- Updated dashboard, transactions, accounts, and categories reads to follow the selected snapshot context by default when no explicit date filter is provided.
- Hardened mutation endpoints so account creation, transaction changes, category writes, and sync actions are rejected while a closed snapshot is active.
- Added dashboard snapshot selection with explicit confirmation plus read-only banners and disabled edit controls across key monthly-finance pages.

## Completion Note

Phase 11 completes the milestone’s historical-month model. Users can move through closed months confidently, and the app now treats preserved history as trustworthy read-only state instead of just another editable period.
