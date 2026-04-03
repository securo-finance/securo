# Structure

## Top-Level Layout

- [`backend`](/workspaces/flux-pluggy/securo/backend): FastAPI application, Celery workers, Alembic migrations, and backend test suite.
- [`frontend`](/workspaces/flux-pluggy/securo/frontend): React/Vite SPA.
- [`docs`](/workspaces/flux-pluggy/securo/docs): marketing/product assets such as the logo and screenshot referenced by the README.
- [`scripts`](/workspaces/flux-pluggy/securo/scripts): local developer helper scripts.
- [`.github/workflows`](/workspaces/flux-pluggy/securo/.github/workflows): CI and release automation.
- [`.codex`](/workspaces/flux-pluggy/securo/.codex): local agent workflow/skill infrastructure, not part of the shipped product runtime.
- [`.planning/codebase`](/workspaces/flux-pluggy/securo/.planning/codebase): generated codebase map documents.

## Backend Layout

- [`backend/app/main.py`](/workspaces/flux-pluggy/securo/backend/app/main.py): FastAPI assembly and route registration.
- [`backend/app/core`](/workspaces/flux-pluggy/securo/backend/app/core): foundational runtime pieces.
  - [`backend/app/core/config.py`](/workspaces/flux-pluggy/securo/backend/app/core/config.py): settings model and env loading.
  - [`backend/app/core/database.py`](/workspaces/flux-pluggy/securo/backend/app/core/database.py): engine/session/base model setup.
  - [`backend/app/core/auth.py`](/workspaces/flux-pluggy/securo/backend/app/core/auth.py): JWT and FastAPI Users wiring.
- [`backend/app/api`](/workspaces/flux-pluggy/securo/backend/app/api): HTTP router modules, usually one per domain.
- [`backend/app/models`](/workspaces/flux-pluggy/securo/backend/app/models): SQLAlchemy models.
- [`backend/app/schemas`](/workspaces/flux-pluggy/securo/backend/app/schemas): Pydantic request/response models.
- [`backend/app/services`](/workspaces/flux-pluggy/securo/backend/app/services): business logic and orchestration.
- [`backend/app/providers`](/workspaces/flux-pluggy/securo/backend/app/providers): provider interfaces and implementations for bank sync, FX rates, and storage.
- [`backend/app/tasks`](/workspaces/flux-pluggy/securo/backend/app/tasks): Celery task entry points.
- [`backend/app/worker.py`](/workspaces/flux-pluggy/securo/backend/app/worker.py): Celery app and beat schedule.
- [`backend/alembic`](/workspaces/flux-pluggy/securo/backend/alembic): migration environment and versioned schema changes.
- [`backend/tests`](/workspaces/flux-pluggy/securo/backend/tests): backend integration/service tests with shared fixtures in [`backend/tests/conftest.py`](/workspaces/flux-pluggy/securo/backend/tests/conftest.py).

## Frontend Layout

- [`frontend/src/main.tsx`](/workspaces/flux-pluggy/securo/frontend/src/main.tsx): React bootstrap.
- [`frontend/src/App.tsx`](/workspaces/flux-pluggy/securo/frontend/src/App.tsx): provider stack and route tree.
- [`frontend/src/pages`](/workspaces/flux-pluggy/securo/frontend/src/pages): route-level screens.
- [`frontend/src/components`](/workspaces/flux-pluggy/securo/frontend/src/components): shared feature components and dialogs.
- [`frontend/src/components/ui`](/workspaces/flux-pluggy/securo/frontend/src/components/ui): low-level design-system primitives.
- [`frontend/src/contexts`](/workspaces/flux-pluggy/securo/frontend/src/contexts): React context state, currently auth.
- [`frontend/src/hooks`](/workspaces/flux-pluggy/securo/frontend/src/hooks): custom hooks such as privacy-mode behavior.
- [`frontend/src/lib`](/workspaces/flux-pluggy/securo/frontend/src/lib): API client, i18n bootstrap, formatting, and utilities.
- [`frontend/src/types`](/workspaces/flux-pluggy/securo/frontend/src/types): shared TypeScript domain types.
- [`frontend/src/locales`](/workspaces/flux-pluggy/securo/frontend/src/locales): translation JSON files.
- [`frontend/public`](/workspaces/flux-pluggy/securo/frontend/public): static web assets.

## Naming And Location Patterns

- Backend API modules are pluralized by domain, for example [`backend/app/api/accounts.py`](/workspaces/flux-pluggy/securo/backend/app/api/accounts.py) and [`backend/app/api/payees.py`](/workspaces/flux-pluggy/securo/backend/app/api/payees.py).
- Backend service modules typically mirror API domains, for example [`backend/app/services/account_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/account_service.py).
- Backend schema/model filenames are singular or near-singular by entity.
- Frontend page filenames map directly to routes, for example [`frontend/src/pages/budgets.tsx`](/workspaces/flux-pluggy/securo/frontend/src/pages/budgets.tsx) for `/budgets`.
- Shared UI components use kebab-case file names and PascalCase exports, for example [`frontend/src/components/page-header.tsx`](/workspaces/flux-pluggy/securo/frontend/src/components/page-header.tsx).

## Where To Look First

- New API capability: start in a router under [`backend/app/api`](/workspaces/flux-pluggy/securo/backend/app/api), then find the paired service.
- DB or entity changes: inspect model in [`backend/app/models`](/workspaces/flux-pluggy/securo/backend/app/models) and migration history in [`backend/alembic/versions`](/workspaces/flux-pluggy/securo/backend/alembic/versions).
- Background job behavior: start with [`backend/app/worker.py`](/workspaces/flux-pluggy/securo/backend/app/worker.py), then task files in [`backend/app/tasks`](/workspaces/flux-pluggy/securo/backend/app/tasks).
- Auth bugs: inspect [`backend/app/core/auth.py`](/workspaces/flux-pluggy/securo/backend/app/core/auth.py), [`frontend/src/contexts/auth-context.tsx`](/workspaces/flux-pluggy/securo/frontend/src/contexts/auth-context.tsx), and [`frontend/src/lib/api.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/api.ts).
- Frontend route or page issues: start in [`frontend/src/App.tsx`](/workspaces/flux-pluggy/securo/frontend/src/App.tsx), then the relevant page in [`frontend/src/pages`](/workspaces/flux-pluggy/securo/frontend/src/pages).

## Structural Observations

- The backend is broad in domain coverage but remains flat at the service-module level; there are many sibling service files rather than deeper subpackages.
- The frontend currently keeps most route logic inside page files rather than splitting each route into smaller co-located feature folders.
- There is no dedicated monorepo package manager workspace; backend and frontend are separate toolchains inside one repo.
