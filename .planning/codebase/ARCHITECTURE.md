# Architecture

## High-Level Shape

Securo is a monorepo with two primary applications:

- Backend API and background worker in `backend/`
- Frontend SPA in `frontend/`

The dominant application pattern is a conventional layered backend plus a route-driven frontend:

- API routers in `backend/app/api/`
- domain services in `backend/app/services/`
- persistence models in `backend/app/models/`
- request/response schemas in `backend/app/schemas/`
- provider adapters in `backend/app/providers/`
- page/components/hooks/lib split in `frontend/src/`

## Backend Request Flow

Typical HTTP flow:

1. Route handler in `backend/app/api/*.py`
2. Auth/session dependencies from `backend/app/core/auth.py` and `backend/app/core/database.py`
3. Domain logic delegated to `backend/app/services/*.py`
4. SQLAlchemy models in `backend/app/models/*.py`
5. Pydantic response schemas in `backend/app/schemas/*.py`

Example:

- `backend/app/api/connections.py` validates/authenticates request
- it calls `backend/app/services/connection_service.py`
- provider-specific work is delegated to `backend/app/providers/pluggy.py`

## Backend Domain Organization

The backend is organized by business capability, with parallel modules across layers:

- accounts
- transactions
- categories and category groups
- rules
- recurring transactions
- budgets
- assets
- reports
- dashboard
- bank connections/imports
- payees
- attachments
- FX/currencies
- setup/settings/auth

This keeps related behavior discoverable, but the service layer is broad and some services are likely becoming large orchestration points.

## Persistence Model

- `backend/app/core/database.py` creates a single async engine/sessionmaker
- SQLAlchemy declarative base lives in the same module
- Models use explicit UUID primary keys and typed mapped columns
- Alembic migrations in `backend/alembic/versions/` evolve schema over time

Representative entities:

- `backend/app/models/user.py`
- `backend/app/models/account.py`
- `backend/app/models/transaction.py`
- `backend/app/models/budget.py`
- `backend/app/models/recurring_transaction.py`
- `backend/app/models/bank_connection.py`
- `backend/app/models/asset.py`

## Background Processing Architecture

- Celery app is initialized in `backend/app/worker.py`
- Synchronous Celery task entry points wrap async service logic with `asyncio.run(...)`
- Periodic jobs are configured directly in code via `celery_app.conf.beat_schedule`
- Background jobs mainly cover sync, recurring generation, asset growth, and FX maintenance

This is a straightforward architecture, but it couples job definitions tightly to app code and duplicates some app-start behavior.

## Startup And Runtime Behavior

- `backend/app/main.py` creates the FastAPI app and mounts all routers
- A lifespan hook dispatches `sync_all_connections` on startup
- CORS currently allows exactly `settings.frontend_url`
- Health endpoint lives at `/api/health`

## Frontend Architecture

- `frontend/src/App.tsx` owns the top-level providers and route tree
- `frontend/src/contexts/auth-context.tsx` manages auth state
- `frontend/src/lib/api.ts` is the main typed API client surface
- `frontend/src/components/app-layout.tsx` is the shell/sidebar layout for authenticated pages
- Pages under `frontend/src/pages/` fetch data directly with React Query and render feature-specific UI

The frontend is a client-rendered SPA. There is no server-side rendering or separate BFF layer.

## Frontend Data Flow

Typical UI flow:

1. Route renders a page component from `frontend/src/pages/*.tsx`
2. Page calls functions from `frontend/src/lib/api.ts`
3. Axios sends requests to `/api`
4. React Query manages request caching/invalidation
5. Dialog/form components mutate data and invalidate query keys

Example:

- `frontend/src/pages/dashboard.tsx` issues multiple parallel queries for summary, spending, balances, budgets, categories, and accounts
- mutation handlers invalidate coarse query prefixes like `['dashboard']` and `['transactions']`

## Cross-Cutting Patterns

- Authentication gates most routes through `frontend/src/components/protected-route.tsx`
- Internationalization uses locale files in `frontend/src/locales/`
- Theme state is handled by `frontend/src/components/theme-provider.tsx`
- Privacy masking is encapsulated in `frontend/src/hooks/use-privacy-mode.ts`

## Entry Points

- Backend API: `backend/app/main.py`
- Backend CLI: `backend/app/cli.py`
- Celery worker/beat: `backend/app/worker.py`
- Frontend app bootstrap: `frontend/src/main.tsx`
- Frontend route shell: `frontend/src/App.tsx`
- Local stack orchestration: `docker-compose.yml`

## Architectural Strengths

- Clear separation between API, services, models, and schemas
- Good feature symmetry across backend and frontend domains
- Async-first backend design aligns with FastAPI/httpx/database usage
- Provider abstraction exists for bank sync and storage concerns

## Architectural Pressure Points

- `frontend/src/lib/api.ts` is a monolithic client surface and central coupling point
- Some page components such as `frontend/src/pages/dashboard.tsx` combine heavy data orchestration and rendering
- Service modules likely carry both business rules and persistence concerns instead of narrower units
- Startup-triggered sync in `backend/app/main.py` overlaps with scheduled sync in `backend/app/worker.py`
