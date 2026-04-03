# Stack

## Overview

Securo is a two-application monorepo with infrastructure glue at the repository root:

- Backend API and background workers in `backend/`
- Frontend SPA in `frontend/`
- Local and production orchestration in `docker-compose.yml` and `docker-compose.prod.yml`
- CI/CD in `.github/workflows/ci.yml` and `.github/workflows/release.yml`

## Backend Stack

- Language: Python 3.11+ declared in `backend/pyproject.toml`
- Web framework: FastAPI in `backend/app/main.py`
- Auth: `fastapi-users` plus project auth helpers in `backend/app/core/auth.py`
- ORM: SQLAlchemy 2 async ORM in `backend/app/core/database.py`
- Migrations: Alembic in `backend/alembic.ini` and `backend/alembic/versions/*.py`
- Validation/settings: Pydantic v2 and `pydantic-settings` in `backend/app/core/config.py`
- HTTP client: `httpx` for upstream provider calls in `backend/app/providers/pluggy.py` and `backend/app/providers/openexchangerates.py`
- Background jobs: Celery with Redis broker/backend in `backend/app/worker.py`
- File parsing: `ofxparse` and stdlib CSV/XML parsing in `backend/app/services/import_service.py`
- File uploads/storage: `aiofiles` plus storage abstraction in `backend/app/providers/storage.py` and `backend/app/providers/local_storage.py`

## Frontend Stack

- Language: TypeScript in `frontend/src/**/*.ts*`
- UI runtime: React 19 in `frontend/package.json`
- Bundler/dev server: Vite 7 in `frontend/vite.config.ts`
- Routing: `react-router-dom` in `frontend/src/App.tsx`
- Data fetching/cache: TanStack Query in `frontend/src/App.tsx`
- HTTP client: Axios in `frontend/src/lib/api.ts`
- Forms/validation: `react-hook-form`, `@hookform/resolvers`, and `zod` in `frontend/package.json`
- Styling: Tailwind CSS v4 via `@tailwindcss/vite`, utility helpers in `frontend/src/lib/utils.ts`, and global styles in `frontend/src/index.css`
- Component primitives: Radix-derived UI components under `frontend/src/components/ui/`
- Icons/charts/toasts: `lucide-react`, `recharts`, and `sonner`
- Internationalization: `i18next` and `react-i18next` in `frontend/src/lib/i18n.ts` with locale files in `frontend/src/locales/`
- Theme management: `next-themes` in `frontend/src/components/theme-provider.tsx`
- Pluggy UI integration: `react-pluggy-connect` in `frontend/package.json`

## Data and Infrastructure

- Primary database: PostgreSQL 16 in `docker-compose.yml`
- Queue/cache: Redis 7 in `docker-compose.yml`
- Attachment storage: named Docker volume mounted to `/app/data/attachments`, configured by `STORAGE_LOCAL_PATH`
- Containerization:
  - Backend dev/prod images from `backend/Dockerfile`
  - Frontend dev image from `frontend/Dockerfile.dev`
  - Frontend prod image from `frontend/Dockerfile`
- Reverse serving in prod: frontend container serves built assets via `frontend/nginx.conf`

## Tooling

- Backend linting: Ruff configured in `backend/pyproject.toml`
- Backend tests: Pytest, `pytest-asyncio`, and coverage config in `backend/pyproject.toml`
- Frontend linting: ESLint flat config in `frontend/eslint.config.js`
- Frontend type/build check: `tsc -b && vite build` from `frontend/package.json`
- Dev helper: Docker hot-deploy helper in `scripts/dev-frontend.mjs`

## Configuration Entry Points

- Root-level optional secrets and integration values in `.env` and `.env.example`
- Backend settings model in `backend/app/core/config.py`
- Backend local env example in `backend/.env.example`
- Frontend runtime backend proxy config in `frontend/vite.config.ts`
- Compose-level environment wiring in `docker-compose.yml` and `docker-compose.prod.yml`

## Current Constraints

- No monorepo package manager/workspace abstraction; backend and frontend are managed independently
- No frontend test runner is configured in `frontend/package.json`
- Production deployment assumes Docker images published to `ghcr.io/securo-finance/*` in `.github/workflows/release.yml`
