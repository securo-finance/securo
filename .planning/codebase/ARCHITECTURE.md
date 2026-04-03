# Architecture

## System Shape

Securo is a two-tier web application with asynchronous backend services and a client-rendered SPA frontend.

- Frontend: React SPA bootstrapped from [`frontend/src/main.tsx`](/workspaces/flux-pluggy/securo/frontend/src/main.tsx), routed in [`frontend/src/App.tsx`](/workspaces/flux-pluggy/securo/frontend/src/App.tsx).
- Backend: FastAPI app assembled in [`backend/app/main.py`](/workspaces/flux-pluggy/securo/backend/app/main.py).
- Persistence: PostgreSQL accessed through SQLAlchemy async sessions in [`backend/app/core/database.py`](/workspaces/flux-pluggy/securo/backend/app/core/database.py).
- Background processing: Celery worker/beat in [`backend/app/worker.py`](/workspaces/flux-pluggy/securo/backend/app/worker.py).

## Backend Layering

The backend follows a conventional router -> service -> model/schema pattern.

- API routers under [`backend/app/api`](/workspaces/flux-pluggy/securo/backend/app/api) define HTTP endpoints, auth/session dependencies, request parsing, and response shaping.
- Service modules under [`backend/app/services`](/workspaces/flux-pluggy/securo/backend/app/services) hold most business logic.
- Models under [`backend/app/models`](/workspaces/flux-pluggy/securo/backend/app/models) define persistence entities.
- Schemas under [`backend/app/schemas`](/workspaces/flux-pluggy/securo/backend/app/schemas) define request/response contracts.
- Core wiring under [`backend/app/core`](/workspaces/flux-pluggy/securo/backend/app/core) provides config, database, and auth primitives.
- Provider abstractions under [`backend/app/providers`](/workspaces/flux-pluggy/securo/backend/app/providers) isolate external systems such as bank sync, FX rates, and storage.
- Task modules under [`backend/app/tasks`](/workspaces/flux-pluggy/securo/backend/app/tasks) wrap recurring or asynchronous workflows for Celery.

## Frontend Layering

- `App` sets up Theme, Query Client, Router, Auth context, and protected layout in [`frontend/src/App.tsx`](/workspaces/flux-pluggy/securo/frontend/src/App.tsx).
- Page-level screens live under [`frontend/src/pages`](/workspaces/flux-pluggy/securo/frontend/src/pages).
- Reusable composite components live under [`frontend/src/components`](/workspaces/flux-pluggy/securo/frontend/src/components).
- Lower-level UI primitives live under [`frontend/src/components/ui`](/workspaces/flux-pluggy/securo/frontend/src/components/ui).
- Cross-page data access is centralized in [`frontend/src/lib/api.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/api.ts).
- Auth state is separated into [`frontend/src/contexts/auth-context.tsx`](/workspaces/flux-pluggy/securo/frontend/src/contexts/auth-context.tsx).
- Utilities and formatting helpers live under [`frontend/src/lib`](/workspaces/flux-pluggy/securo/frontend/src/lib).

## Main Execution Paths

### Request/Response Path

1. Browser route loads a page component from [`frontend/src/pages`](/workspaces/flux-pluggy/securo/frontend/src/pages).
2. The page uses TanStack Query or direct mutations with API methods from [`frontend/src/lib/api.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/api.ts).
3. FastAPI router receives the request in a file such as [`backend/app/api/transactions.py`](/workspaces/flux-pluggy/securo/backend/app/api/transactions.py).
4. The router resolves auth and DB session dependencies from [`backend/app/core/auth.py`](/workspaces/flux-pluggy/securo/backend/app/core/auth.py) and [`backend/app/core/database.py`](/workspaces/flux-pluggy/securo/backend/app/core/database.py).
5. Router delegates to a service module such as [`backend/app/services/transaction_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/transaction_service.py).
6. Service reads/writes SQLAlchemy models, commits, and the router returns a schema-validated response.

### Bank Sync Path

1. Frontend requests a connect token or callback handling through `connections.*` in [`frontend/src/lib/api.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/api.ts).
2. Connections router delegates to [`backend/app/services/connection_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/connection_service.py).
3. The service resolves a provider through [`backend/app/providers/__init__.py`](/workspaces/flux-pluggy/securo/backend/app/providers/__init__.py).
4. Provider implementation in [`backend/app/providers/pluggy.py`](/workspaces/flux-pluggy/securo/backend/app/providers/pluggy.py) fetches accounts and transactions.
5. The service persists connections/accounts/transactions, applies category rules, payee mapping, FX stamping, and transfer detection.
6. Scheduled refreshes are triggered from [`backend/app/tasks/sync_tasks.py`](/workspaces/flux-pluggy/securo/backend/app/tasks/sync_tasks.py) through Celery.

### Startup/Background Path

- FastAPI startup in [`backend/app/main.py`](/workspaces/flux-pluggy/securo/backend/app/main.py) attempts to enqueue sync-all-connections.
- Celery beat schedules periodic jobs in [`backend/app/worker.py`](/workspaces/flux-pluggy/securo/backend/app/worker.py).
- Task modules create their own DB sessions and call service-layer functions.

## Architectural Decisions Visible In Code

- Thin routers, heavier services: routers mostly validate/input-map and translate `ValueError` to HTTP errors.
- Async-first backend: endpoints, sessions, providers, and services are `async def`.
- Central API client on frontend: one axios instance handles auth and base URL concerns.
- Protected-shell routing: authenticated routes are nested beneath [`frontend/src/components/protected-route.tsx`](/workspaces/flux-pluggy/securo/frontend/src/components/protected-route.tsx) and [`frontend/src/components/app-layout.tsx`](/workspaces/flux-pluggy/securo/frontend/src/components/app-layout.tsx).
- Domain-oriented backend modules: accounts, budgets, rules, payees, assets, recurring, reports, dashboard, import/export.

## Cross-Cutting Concerns

- Auth and user ownership checks are repeated across service queries.
- Multi-currency support is cross-cutting: transactions can track `amount_primary` and `fx_rate_used`; dashboard/reporting code consumes primary-currency totals.
- Attachment handling spans API/service/model/storage-provider layers.
- Internationalization is frontend-only and wired through [`frontend/src/lib/i18n.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/i18n.ts) with locale files under [`frontend/src/locales`](/workspaces/flux-pluggy/securo/frontend/src/locales).

## Boundaries That Are Less Formal

- Service modules are plain functions rather than injected service classes, so shared logic is imported directly across modules.
- There is no separate repository/data-access layer between services and SQLAlchemy queries.
- Frontend page components can become large and combine data loading, formatting, interaction state, and rendering in one file, as seen in [`frontend/src/pages/dashboard.tsx`](/workspaces/flux-pluggy/securo/frontend/src/pages/dashboard.tsx).
