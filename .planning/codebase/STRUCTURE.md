# Structure

## Top-Level Layout

- `backend/`: Python API, Celery worker, Alembic migrations, and backend tests
- `frontend/`: React/Vite SPA, reusable components, and static assets
- `.github/`: CI and release workflows plus issue/PR templates
- `docs/`: branding and screenshot assets used by the README
- `favicons/`: generated favicon asset set
- `scripts/`: small developer utility scripts
- `.codex/`: local Codex/GSD workflow assets, agents, and skills
- `.planning/`: generated planning artifacts; `codebase/` is the map created here

## Backend Directory Guide

- `backend/app/main.py`: API composition root
- `backend/app/core/`: settings, auth helpers, and database/session setup
- `backend/app/api/`: HTTP routers grouped by domain
- `backend/app/models/`: SQLAlchemy models
- `backend/app/schemas/`: request/response models
- `backend/app/services/`: business logic modules
- `backend/app/providers/`: vendor/storage/provider abstractions
- `backend/app/tasks/`: Celery task implementations
- `backend/app/worker.py`: Celery app configuration
- `backend/alembic/`: migration environment and revision history
- `backend/tests/`: backend integration/service tests using pytest + HTTPX ASGI transport

### Backend Naming/Location Patterns

- Domain folders are flattened by type rather than nested by feature
- Matching file triplets are common:
  - `backend/app/api/transactions.py`
  - `backend/app/models/transaction.py`
  - `backend/app/schemas/transaction.py`
  - `backend/app/services/transaction_service.py`
- Services usually use `_service.py` suffix
- Background tasks use `_tasks.py` suffix

## Frontend Directory Guide

- `frontend/src/main.tsx`: browser bootstrap
- `frontend/src/App.tsx`: providers and route definitions
- `frontend/src/pages/`: route-level screens
- `frontend/src/components/`: shared feature components
- `frontend/src/components/ui/`: base UI primitives
- `frontend/src/contexts/`: React context providers such as auth
- `frontend/src/hooks/`: custom hooks like privacy mode
- `frontend/src/lib/`: API client, i18n, formatting, and helpers
- `frontend/src/locales/`: translation JSON bundles
- `frontend/src/types/`: shared frontend TypeScript types
- `frontend/public/`: browser icons and favicon assets

### Frontend Naming/Location Patterns

- Route files are lowercase kebab-style without nested route directories, for example `frontend/src/pages/account-detail.tsx`
- Shared components are kebab-case files in `frontend/src/components/`
- UI primitives use one component per file under `frontend/src/components/ui/`
- Aliased imports use `@/` resolved by `frontend/vite.config.ts`

## Infrastructure and Ops Files

- `docker-compose.yml`: primary local/dev stack
- `docker-compose.prod.yml`: production-like container stack using published images
- `backend/Dockerfile`: backend image
- `frontend/Dockerfile`: production frontend image
- `frontend/Dockerfile.dev`: dev frontend image
- `frontend/nginx.conf`: frontend production serving config
- `.github/workflows/ci.yml`: backend/frontend checks
- `.github/workflows/release.yml`: multi-arch image publish on release

## Where To Look First

- New API/domain behavior: start at `backend/app/api/` then the matching file in `backend/app/services/`
- Schema/database changes: check `backend/app/models/` and `backend/alembic/versions/`
- External provider behavior: inspect `backend/app/providers/` and `backend/app/services/connection_service.py`
- Background or scheduled automation: inspect `backend/app/worker.py` and `backend/app/tasks/`
- Frontend screen behavior: start at `frontend/src/pages/` and follow calls into `frontend/src/lib/api.ts`
- Authentication/session issues: inspect `backend/app/core/auth.py` and `frontend/src/contexts/auth-context.tsx`
- Build/runtime env issues: inspect the relevant compose file plus `backend/app/core/config.py` and `frontend/vite.config.ts`

## Observed Structural Characteristics

- This is a practical monorepo, not a formal workspace-managed monorepo
- The backend has broad domain coverage already; most business areas have API/service/model/schema coverage
- The frontend is feature-dense but still relatively flat, which keeps discovery easy at current size
- There is no dedicated `shared/` package between frontend and backend; contracts are duplicated through backend schemas and frontend TS types
