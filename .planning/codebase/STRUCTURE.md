# Structure

## Top-Level Layout

- `backend/` backend application, migrations, tests, packaging
- `frontend/` React SPA source, build config, static assets
- `docs/` branding and screenshot assets
- `favicons/` favicon source assets
- `scripts/` local development helpers
- `.github/workflows/` CI
- `.codex/` GSD workflow and skill infrastructure

## Backend Layout

### Application Code

- `backend/app/main.py` FastAPI app assembly
- `backend/app/core/` config, database, auth primitives
- `backend/app/api/` route modules grouped by domain
- `backend/app/services/` business logic and orchestration
- `backend/app/models/` SQLAlchemy models
- `backend/app/schemas/` Pydantic request/response models
- `backend/app/providers/` external integration adapters and storage/provider abstractions
- `backend/app/tasks/` Celery task entry points

### Database And Packaging

- `backend/alembic/` migration environment
- `backend/alembic/versions/` chronological schema changes
- `backend/pyproject.toml` Python package/test/lint configuration
- `backend/Dockerfile` backend image build

### Tests

- `backend/tests/` pytest suite
- `backend/tests/conftest.py` DB/app fixtures and dependency overrides

## Frontend Layout

### Source Tree

- `frontend/src/main.tsx` React bootstrap
- `frontend/src/App.tsx` provider composition and routes
- `frontend/src/pages/` route-level feature screens
- `frontend/src/components/` app shell, dialogs, domain widgets
- `frontend/src/components/ui/` reusable UI primitives
- `frontend/src/contexts/` global React context providers
- `frontend/src/hooks/` reusable hooks
- `frontend/src/lib/` API client, formatting, i18n, utility helpers
- `frontend/src/types/` shared TypeScript API/domain types
- `frontend/src/locales/` translation JSON files

### Frontend Config And Assets

- `frontend/package.json` scripts and dependencies
- `frontend/vite.config.ts` dev/build config
- `frontend/eslint.config.js` lint rules
- `frontend/index.html` Vite HTML entry
- `frontend/public/` static icons and favicons
- `frontend/nginx.conf` deployment web-server config
- `frontend/Dockerfile` and `frontend/Dockerfile.dev` image definitions

## Naming And Module Patterns

- Backend filenames are mostly snake_case by domain, for example `backend/app/services/dashboard_service.py`
- Frontend page/component files use kebab-case or lower-case route naming, for example `frontend/src/pages/account-detail.tsx`
- UI primitives in `frontend/src/components/ui/` match component names, for example `button.tsx`, `dialog.tsx`, `table.tsx`
- Alembic migration files use numeric prefixes and short descriptions, for example `backend/alembic/versions/019_payees.py`

## Key Files To Read First

For backend orientation:

- `backend/app/main.py`
- `backend/app/core/config.py`
- `backend/app/core/database.py`
- `backend/app/core/auth.py`
- one representative router/service pair such as `backend/app/api/connections.py` and `backend/app/services/connection_service.py`

For frontend orientation:

- `frontend/src/App.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/contexts/auth-context.tsx`
- `frontend/src/components/app-layout.tsx`
- one representative page such as `frontend/src/pages/dashboard.tsx`

For environment/runtime orientation:

- `README.md`
- `docker-compose.yml`
- `.github/workflows/ci.yml`

## Structural Observations

- The codebase is feature-rich rather than library-like; most directories map to product capabilities
- The backend has stronger layering than the frontend, where pages often coordinate many queries and dialogs directly
- There is no shared package between backend and frontend; type alignment appears hand-maintained in `frontend/src/types/index.ts`
- Tests are concentrated in the backend; no `frontend/src/**/*.test.*` files were found during this mapping pass
