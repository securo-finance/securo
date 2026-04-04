---
phase: 08-current-month-setup-guards
plan: 01
subsystem: month-setup
tags:
  - monthly-periods
  - guards
  - dashboard
  - ux
provides:
  - Dedicated current-month API backed by user preferences
  - Shared backend guard that blocks sync and manual creation until `Mês Atual` exists
  - Dashboard-first month setup UX and inline lock states on accounts, transactions, and categories pages
affects: []
tech-stack:
  added: []
  patterns:
    - Current-month state is consumed through one backend contract and one frontend query hook
key-files:
  created:
    - backend/app/api/months.py
    - backend/app/schemas/month.py
    - backend/app/services/month_service.py
    - backend/tests/test_months_api.py
    - frontend/src/hooks/use-current-month.ts
    - .planning/phases/08-current-month-setup-guards/08-CONTEXT.md
    - .planning/phases/08-current-month-setup-guards/08-UI-SPEC.md
    - .planning/phases/08-current-month-setup-guards/08-01-PLAN.md
    - .planning/phases/08-current-month-setup-guards/08-VERIFICATION.md
  modified:
    - backend/app/main.py
    - backend/app/api/accounts.py
    - backend/app/api/categories.py
    - backend/app/api/connections.py
    - backend/app/api/transactions.py
    - backend/tests/conftest.py
    - backend/tests/test_accounts_api.py
    - backend/tests/test_categories_api.py
    - backend/tests/test_connections_api.py
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
  - Persist the active `Mês Atual` as a user-preference-backed monthly period with a dedicated API instead of scattering implicit defaults
  - Enforce the same current-month lock in backend mutation endpoints and in visible frontend action states
patterns-established:
  - Month setup lives in the existing dashboard layout and other pages consume the shared current-month hook for guard state
requirements-completed:
  - PERIOD-01
  - PERIOD-03
  - GUARD-02
  - GUARD-03
  - UX-01
duration: "55min"
completed: 2026-04-04
---

# Phase 8: Current Month Setup & Guards Summary

**Flux now has an explicit `Mês Atual` contract, and the app blocks sync plus manual creation flows until that monthly period is defined.**

## Accomplishments

- Added `/api/months/current` with normalized month-period parsing and a shared backend service for reading or updating the active month.
- Applied the current-month guard to account creation, category creation, transaction creation, transfer creation, and bank sync.
- Updated the dashboard to become the month-setup entry point and added inline locked-state messaging to accounts, transactions, and categories while preserving the existing Flux layout pattern.
- Added backend regression coverage for the month API and the guarded mutation flows.

## Completion Note

Phase 8 establishes the single source of truth for the editable month and the lock behavior that future monthly-snapshot phases will build on. Historical snapshot browsing is still deferred to later phases, but the app no longer silently allows current-month mutations without an explicit monthly competency.
