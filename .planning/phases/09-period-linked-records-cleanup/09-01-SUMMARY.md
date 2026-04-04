---
phase: 09-period-linked-records-cleanup
plan: 01
subsystem: period-linking
tags:
  - monthly-periods
  - records
  - cleanup
  - export
provides:
  - Durable `monthly_periods` persistence backing the active month preference
  - Account and transaction records linked to monthly periods across manual, sync, and import flows
  - Removal of supported category-group API/model/export surface
affects: []
tech-stack:
  added:
    - SQLAlchemy model for `monthly_periods`
    - Alembic migration `021`
  patterns:
    - Current-month reads resolve period-aware filters first and only fall back to raw dates for older data
key-files:
  created:
    - backend/app/models/monthly_period.py
    - backend/alembic/versions/021_monthly_periods_and_category_group_cleanup.py
    - .planning/phases/09-period-linked-records-cleanup/09-01-SUMMARY.md
    - .planning/phases/09-period-linked-records-cleanup/09-VERIFICATION.md
  modified:
    - backend/app/api/export.py
    - backend/app/models/__init__.py
    - backend/app/models/account.py
    - backend/app/models/category.py
    - backend/app/models/transaction.py
    - backend/app/models/user.py
    - backend/app/schemas/account.py
    - backend/app/schemas/transaction.py
    - backend/app/services/account_service.py
    - backend/app/services/connection_service.py
    - backend/app/services/dashboard_service.py
    - backend/app/services/import_service.py
    - backend/app/services/month_service.py
    - backend/app/services/transaction_service.py
    - backend/tests/conftest.py
    - backend/tests/test_accounts_api.py
    - backend/tests/test_category_service.py
    - backend/tests/test_export_api.py
    - backend/tests/test_months_api.py
    - backend/tests/test_setup_api.py
    - backend/tests/test_transactions_api.py
    - frontend/src/types/index.ts
  deleted:
    - backend/app/api/category_groups.py
    - backend/app/models/category_group.py
    - backend/app/schemas/category_group.py
    - backend/app/services/category_group_service.py
    - backend/tests/test_category_group_service.py
key-decisions:
  - Persist monthly periods in a dedicated table keyed by user and `YYYY-MM` instead of treating them as preference-only state
  - Link historical sync/import transactions to the transaction month while linking manual current-month mutations to the active editable period
  - Remove category-group compatibility from the supported runtime and backup surface in this phase instead of carrying it into snapshot work
patterns-established:
  - Supported month-aware reads use monthly-period linkage first and keep date fallback only where historical compatibility is still needed
requirements-completed:
  - DATA-01
  - DATA-02
  - CLEAN-01
  - CLEAN-02
duration: "70min"
completed: 2026-04-04
---

# Phase 9: Period-Linked Records & Cleanup Summary

**Flux financial records now carry durable monthly-period identity, and the supported product surface no longer depends on category groups.**

## Accomplishments

- Added persisted `monthly_periods` plus shared helpers that create or resolve the active month as a database record instead of keeping it only in user preferences.
- Linked account creation, opening balances, manual transactions, transfers, sync imports, and file imports to monthly periods, using the transaction month for imported or synced historical data.
- Switched current-month transaction and dashboard reads to prefer period linkage, while retaining date fallback where older non-linked data may still exist.
- Removed supported category-group exposure from the backend runtime and backup/export contract, and deleted the obsolete API/service/schema/model test surface.

## Completion Note

Phase 9 establishes the durable month key that snapshot closure will build on next. The app can now separate editable-month records from historical-month records structurally, rather than inferring everything from raw dates or legacy category-group relationships.
