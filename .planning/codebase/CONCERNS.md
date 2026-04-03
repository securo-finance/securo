# Concerns

## High-Value Risk Areas

### Large Frontend Page Components

- Some route files, especially [`frontend/src/pages/dashboard.tsx`](/workspaces/flux-pluggy/securo/frontend/src/pages/dashboard.tsx), are large and combine formatting helpers, query wiring, mutation handling, local state, and rendering.
- This makes behavioral changes harder to isolate and raises regression risk when touching dashboard interactions or calculations.

### Frontend Test Coverage Gap

- The frontend has lint/build checks but no unit, integration, or browser test harness in [`frontend/package.json`](/workspaces/flux-pluggy/securo/frontend/package.json).
- High-interaction components such as [`frontend/src/components/transaction-dialog.tsx`](/workspaces/flux-pluggy/securo/frontend/src/components/transaction-dialog.tsx), [`frontend/src/components/bank-connect-dialog.tsx`](/workspaces/flux-pluggy/securo/frontend/src/components/bank-connect-dialog.tsx), and multiple pages are therefore protected only by manual testing.

### Provider And Background-Job Fragility

- Critical sync behavior depends on third-party APIs and background execution through Pluggy, Open Exchange Rates, Redis, and Celery.
- Startup dispatch in [`backend/app/main.py`](/workspaces/flux-pluggy/securo/backend/app/main.py) catches broad exceptions and logs them, which prevents crashes but can hide broken background wiring until data stops syncing.
- Task modules use `asyncio.run(...)` wrappers, which are workable but can be awkward to test and debug.

### Test Environment Drift

- Backend tests run against SQLite in [`backend/tests/conftest.py`](/workspaces/flux-pluggy/securo/backend/tests/conftest.py), while production uses PostgreSQL/asyncpg.
- That reduces setup cost, but it can hide Postgres-only issues around SQL behavior, migrations, data types, and performance.

## Domain-Specific Complexity

### Multi-Currency Logic

- FX stamping, primary-currency amounts, fallback exchange rates, and reporting conversions cross several modules:
  - [`backend/app/services/fx_rate_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/fx_rate_service.py)
  - [`backend/app/services/transaction_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/transaction_service.py)
  - [`backend/app/services/dashboard_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/dashboard_service.py)
  - [`backend/app/api/transactions.py`](/workspaces/flux-pluggy/securo/backend/app/api/transactions.py)
- This is a feature differentiator, but it raises the cost of “simple” transaction changes.

### Sync Import Side Effects

- Connection sync in [`backend/app/services/connection_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/connection_service.py) does many things in one flow: create connection/accounts, import transactions, resolve payees, map categories, stamp FX data, and detect transfers.
- That centralization is practical, but it concentrates failure modes and makes partial-sync edge cases harder to reason about.

### Attachment Storage Mismatch

- Config includes future-facing S3 settings in [`backend/app/core/config.py`](/workspaces/flux-pluggy/securo/backend/app/core/config.py), but only local storage implementation was found.
- If the product messaging or deployment assumptions imply pluggable cloud storage today, the code does not appear fully there yet.

## Maintainability Concerns

- Service modules are numerous and flat. Discoverability is still reasonable, but cross-service imports can become tangled as features grow.
- There is no dedicated repository/data-access layer, so complex queries and business rules can mix within the same function.
- Some runtime behavior is encoded in comments and conventions rather than stronger abstractions, for example recurring idempotency assumptions in [`backend/app/worker.py`](/workspaces/flux-pluggy/securo/backend/app/worker.py).

## Operational Concerns

- The app depends on Docker Compose for the happy path, which is fine for self-hosting but means local debugging outside containers requires more manual setup.
- Frontend auth relies on `localStorage` and hard redirect on `401` in [`frontend/src/lib/api.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/api.ts); this is simple, but abrupt and potentially awkward for concurrent-tab/session-expiry UX.
- Scheduled sync and recurring jobs are time-based, not event-driven, so some freshness/latency tradeoffs are structural.

## Positive Signals

- The backend test suite is broad and domain-oriented.
- The architecture is understandable without deep framework indirection.
- External integration points are at least partially abstracted behind provider interfaces, which should help future replacement or extension work.
