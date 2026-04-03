# Stack

## Overview

Securo is a self-hosted personal finance application with a Python API, a React SPA, PostgreSQL for persistence, and Redis/Celery for background jobs. Local development and production both center around Docker Compose: [`docker-compose.yml`](/workspaces/flux-pluggy/securo/docker-compose.yml) for development and [`docker-compose.prod.yml`](/workspaces/flux-pluggy/securo/docker-compose.prod.yml) for prebuilt images.

## Backend

- Language/runtime: Python 3.11+ declared in [`backend/pyproject.toml`](/workspaces/flux-pluggy/securo/backend/pyproject.toml).
- Web framework: FastAPI in [`backend/app/main.py`](/workspaces/flux-pluggy/securo/backend/app/main.py).
- Auth: `fastapi-users` with JWT bearer auth in [`backend/app/core/auth.py`](/workspaces/flux-pluggy/securo/backend/app/core/auth.py).
- ORM/database access: SQLAlchemy 2 async engine/session in [`backend/app/core/database.py`](/workspaces/flux-pluggy/securo/backend/app/core/database.py).
- Migrations: Alembic via [`backend/alembic.ini`](/workspaces/flux-pluggy/securo/backend/alembic.ini) and [`backend/alembic/versions`](/workspaces/flux-pluggy/securo/backend/alembic/versions).
- Validation/settings: Pydantic v2 and `pydantic-settings` in [`backend/app/core/config.py`](/workspaces/flux-pluggy/securo/backend/app/core/config.py).
- Async HTTP clients: `httpx` for provider integrations in files like [`backend/app/providers/pluggy.py`](/workspaces/flux-pluggy/securo/backend/app/providers/pluggy.py) and [`backend/app/providers/openexchangerates.py`](/workspaces/flux-pluggy/securo/backend/app/providers/openexchangerates.py).
- File uploads/storage: `aiofiles` and storage provider abstraction in [`backend/app/providers/storage.py`](/workspaces/flux-pluggy/securo/backend/app/providers/storage.py) with local implementation in [`backend/app/providers/local_storage.py`](/workspaces/flux-pluggy/securo/backend/app/providers/local_storage.py).

## Frontend

- Language/runtime: TypeScript on Node 22 in CI, configured via [`frontend/package.json`](/workspaces/flux-pluggy/securo/frontend/package.json) and [`.github/workflows/ci.yml`](/workspaces/flux-pluggy/securo/.github/workflows/ci.yml).
- Bundler/dev server: Vite in [`frontend/vite.config.ts`](/workspaces/flux-pluggy/securo/frontend/vite.config.ts).
- UI framework: React 19 in [`frontend/package.json`](/workspaces/flux-pluggy/securo/frontend/package.json).
- Routing: `react-router-dom` route tree in [`frontend/src/App.tsx`](/workspaces/flux-pluggy/securo/frontend/src/App.tsx).
- Server state/data fetching: TanStack Query in [`frontend/src/App.tsx`](/workspaces/flux-pluggy/securo/frontend/src/App.tsx) and page components such as [`frontend/src/pages/dashboard.tsx`](/workspaces/flux-pluggy/securo/frontend/src/pages/dashboard.tsx).
- Forms/validation: `react-hook-form`, `@hookform/resolvers`, and `zod` are installed; page/component usage is spread through dialogs and form components under [`frontend/src/components`](/workspaces/flux-pluggy/securo/frontend/src/components).
- Styling: Tailwind CSS v4, `tw-animate-css`, and shadcn-generated primitives in [`frontend/src/index.css`](/workspaces/flux-pluggy/securo/frontend/src/index.css) and [`frontend/src/components/ui`](/workspaces/flux-pluggy/securo/frontend/src/components/ui).
- Charts/icons/i18n: `recharts`, `lucide-react`, `i18next`, and `react-i18next`.

## Infrastructure

- Database: PostgreSQL 16 in both compose files.
- Queue/cache: Redis 7 and Celery worker/beat defined in [`backend/app/worker.py`](/workspaces/flux-pluggy/securo/backend/app/worker.py).
- Containers:
  - Backend image/build: [`backend/Dockerfile`](/workspaces/flux-pluggy/securo/backend/Dockerfile)
  - Frontend dev container: [`frontend/Dockerfile.dev`](/workspaces/flux-pluggy/securo/frontend/Dockerfile.dev)
  - Frontend prod image referenced from [`docker-compose.prod.yml`](/workspaces/flux-pluggy/securo/docker-compose.prod.yml)
- Local helper script: [`scripts/dev-frontend.mjs`](/workspaces/flux-pluggy/securo/scripts/dev-frontend.mjs) rebuilds Vite output and copies it into a running Docker container.

## Tooling And Quality Gates

- Backend linting: Ruff configured in [`backend/pyproject.toml`](/workspaces/flux-pluggy/securo/backend/pyproject.toml).
- Backend tests: pytest + pytest-asyncio + pytest-cov from [`backend/pyproject.toml`](/workspaces/flux-pluggy/securo/backend/pyproject.toml).
- Frontend linting: ESLint flat config in [`frontend/eslint.config.js`](/workspaces/flux-pluggy/securo/frontend/eslint.config.js).
- Frontend build/type check: `npm run build` in [`frontend/package.json`](/workspaces/flux-pluggy/securo/frontend/package.json).
- CI: GitHub Actions workflow in [`.github/workflows/ci.yml`](/workspaces/flux-pluggy/securo/.github/workflows/ci.yml).

## Configuration Entry Points

- Repo-wide example env: [`.env.example`](/workspaces/flux-pluggy/securo/.env.example).
- Backend example env: [`backend/.env.example`](/workspaces/flux-pluggy/securo/backend/.env.example).
- Runtime settings model: [`backend/app/core/config.py`](/workspaces/flux-pluggy/securo/backend/app/core/config.py).
- Frontend environment usage appears to be mostly container-injected through compose; the SPA itself uses relative `/api` requests via [`frontend/src/lib/api.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/api.ts).

## Notable Stack Characteristics

- The backend is fully async at the framework/database boundary, but Celery tasks bridge into async code with `asyncio.run(...)` in task modules such as [`backend/app/tasks/sync_tasks.py`](/workspaces/flux-pluggy/securo/backend/app/tasks/sync_tasks.py).
- The frontend is an SPA with lazy-loaded route components, not Next.js despite `next-themes` being present.
- There is no dedicated frontend test runner configured in [`frontend/package.json`](/workspaces/flux-pluggy/securo/frontend/package.json) at the time of mapping.
