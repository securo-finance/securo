# Stack

## Overview

Securo is a full-stack self-hosted personal finance application with:

- Python backend in `backend/app/`
- React + TypeScript frontend in `frontend/src/`
- PostgreSQL persistence via SQLAlchemy + Alembic
- Redis-backed Celery worker and beat processes for background jobs
- Docker Compose local orchestration in `docker-compose.yml`

## Backend Runtime

- Python 3.11+ declared in `backend/pyproject.toml`
- FastAPI application entry point in `backend/app/main.py`
- Uvicorn used for local/dev serving via Docker command in `docker-compose.yml`
- Async SQLAlchemy engine/session in `backend/app/core/database.py`
- Pydantic Settings for configuration in `backend/app/core/config.py`
- FastAPI Users for auth plumbing in `backend/app/core/auth.py`

## Backend Libraries

Core dependencies from `backend/pyproject.toml`:

- `fastapi` for HTTP API
- `sqlalchemy` + `asyncpg` for async ORM/database access
- `alembic` for schema migrations in `backend/alembic/`
- `pydantic` + `pydantic-settings` for schemas/config
- `fastapi-users[sqlalchemy]`, `python-jose`, `passlib[bcrypt]` for auth
- `httpx` for outbound API calls
- `ofxparse` for import parsing
- `celery[redis]` for async jobs
- `aiofiles` for attachment storage

## Frontend Runtime

- React 19 app bootstrapped in `frontend/src/main.tsx`
- Route tree in `frontend/src/App.tsx`
- Vite 7 build/dev server in `frontend/vite.config.ts`
- TypeScript 5.9 config in `frontend/tsconfig*.json`
- Browser SPA routing via `react-router-dom`
- Query/cache state via `@tanstack/react-query`

## Frontend Libraries

Notable dependencies from `frontend/package.json`:

- `axios` in `frontend/src/lib/api.ts` for API access
- `react-hook-form`, `zod`, `@hookform/resolvers` for forms/validation
- `i18next`, `react-i18next` for localization in `frontend/src/lib/i18n.ts`
- `next-themes` for theme state in `frontend/src/components/theme-provider.tsx`
- `recharts` for dashboards/reports
- `react-pluggy-connect` for Pluggy connection flows
- Radix-based UI primitives and shadcn-style components in `frontend/src/components/ui/`
- Tailwind CSS v4 via `@tailwindcss/vite`

## Data Layer

- PostgreSQL is the primary runtime database in `docker-compose.yml`
- SQLAlchemy declarative models live under `backend/app/models/`
- Alembic versioned migrations live in `backend/alembic/versions/`
- SQLite is used for tests through `backend/tests/conftest.py`

## Background Processing

- Celery app configured in `backend/app/worker.py`
- Scheduled jobs include:
  - bank sync via `backend/app/tasks/sync_tasks.py`
  - recurring generation via `backend/app/tasks/recurring_tasks.py`
  - asset growth via `backend/app/tasks/asset_tasks.py`
  - FX sync/backfill via `backend/app/tasks/fx_rate_tasks.py` and `backend/app/tasks/fx_backfill_tasks.py`
- Broker/result backend is Redis from `backend/app/core/config.py`

## Tooling

- Ruff configured in `backend/pyproject.toml`
- ESLint flat config in `frontend/eslint.config.js`
- GitHub Actions CI in `.github/workflows/ci.yml`
- Dockerfiles:
  - `backend/Dockerfile`
  - `frontend/Dockerfile`
  - `frontend/Dockerfile.dev`
- Local frontend dev helper in `scripts/dev-frontend.mjs`

## Build And Serving

- Local dev stack is `docker compose up --build` from `README.md`
- Frontend dev server proxies `/api` to backend in `frontend/vite.config.ts`
- Nginx config exists for frontend deployment in `frontend/nginx.conf`
- Production compose variant exists in `docker-compose.prod.yml`

## Configuration Entry Points

- Backend env settings are defined in `backend/app/core/config.py`
- Compose-level env wiring is in `docker-compose.yml`
- Frontend runtime host/proxy inputs are in `frontend/vite.config.ts`
- Optional provider keys documented in `README.md`:
  - `PLUGGY_CLIENT_ID`
  - `PLUGGY_CLIENT_SECRET`
  - `OPENEXCHANGERATES_APP_ID`

## Notable Domain Areas

- Accounts: `backend/app/api/accounts.py`, `frontend/src/pages/accounts.tsx`
- Transactions: `backend/app/api/transactions.py`, `frontend/src/pages/transactions.tsx`
- Connections/sync: `backend/app/api/connections.py`, `frontend/src/components/bank-connect-dialog.tsx`
- Budgets/recurring/rules: matching `api/`, `services/`, and `pages/` modules
- Assets/reports/dashboard: `backend/app/services/dashboard_service.py`, `frontend/src/pages/dashboard.tsx`, `frontend/src/pages/reports.tsx`
