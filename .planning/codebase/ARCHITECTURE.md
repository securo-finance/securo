# Architecture

## System Shape

Securo is a self-hosted personal-finance system split into:

- A FastAPI backend in `backend/app/`
- A React single-page app in `frontend/src/`
- Celery workers for asynchronous and scheduled tasks in `backend/app/worker.py`
- PostgreSQL and Redis managed by Docker Compose at the repo root

The dominant pattern is a layered CRUD-plus-domain-services backend with a page-oriented frontend consuming a typed API wrapper.

## Backend Layers

### API Layer

- HTTP routers live in `backend/app/api/*.py`
- `backend/app/main.py` assembles routers, auth routes, middleware, and startup behavior
- Routers are thin: they validate request/query parameters, fetch the current user/session via dependency injection, and delegate to services

### Service Layer

- Domain logic lives in `backend/app/services/*.py`
- Services handle:
  - ownership checks
  - query composition
  - rule application
  - FX stamping/conversion
  - connection sync/import orchestration
  - transfer detection and attachment management
- Example: `backend/app/services/transaction_service.py` owns filtering, pagination, creation, transfer pairing, and primary-currency stamping behavior

### Persistence Layer

- SQLAlchemy models live in `backend/app/models/*.py`
- Pydantic schemas for API contracts live in `backend/app/schemas/*.py`
- Async session creation is centralized in `backend/app/core/database.py`
- Migration history is maintained under `backend/alembic/versions/`

### Provider/Integration Layer

- Provider interfaces and implementations live in `backend/app/providers/`
- This abstracts:
  - bank providers such as Pluggy
  - FX providers such as Open Exchange Rates
  - attachment storage

### Background Task Layer

- Celery app configuration in `backend/app/worker.py`
- Task modules under `backend/app/tasks/`
- Startup in `backend/app/main.py` dispatches a stale-connection sync task immediately on app boot

## Frontend Layers

### Application Shell

- `frontend/src/main.tsx` mounts the app and global CSS/i18n
- `frontend/src/App.tsx` composes providers and route definitions
- The main shell layout is `frontend/src/components/app-layout.tsx`

### Routing and Access Control

- Public routes: setup/login/register
- Protected application routes render inside `ProtectedRoute` and `AppLayout`
- Route modules live in `frontend/src/pages/*.tsx`

### Client Data Layer

- Central API wrapper: `frontend/src/lib/api.ts`
- Query caching/orchestration: TanStack Query from `frontend/src/App.tsx` and page-level hooks
- Authentication state is centralized in `frontend/src/contexts/auth-context.tsx`

### UI Layer

- Reusable feature components live in `frontend/src/components/`
- Primitive UI building blocks live in `frontend/src/components/ui/`
- Cross-cutting UX concerns include theme, privacy mode, onboarding, and internationalization

## Main Execution Paths

### Authenticated User Request

1. Frontend route/page calls a method from `frontend/src/lib/api.ts`
2. Axios injects bearer token from `localStorage`
3. FastAPI router in `backend/app/api/*.py` resolves current user and DB session
4. Service method in `backend/app/services/*.py` performs business logic and DB access
5. SQLAlchemy models are loaded/persisted through async sessions
6. Router serializes response through Pydantic schema models
7. Frontend updates cached/query-driven UI

### Bank Sync Flow

1. Frontend requests providers/connect token through `frontend/src/lib/api.ts`
2. `backend/app/api/connections.py` delegates to `backend/app/services/connection_service.py`
3. Provider-specific behavior is executed through `backend/app/providers/pluggy.py`
4. Accounts/transactions are persisted and normalized into internal models
5. Celery tasks in `backend/app/tasks/sync_tasks.py` and startup dispatch in `backend/app/main.py` automate refreshes

### Transaction Import Flow

1. User uploads OFX/QIF/CAMT/CSV through frontend import UI in `frontend/src/pages/import.tsx`
2. Backend endpoint in `backend/app/api/import_transactions.py` delegates to `backend/app/services/import_service.py`
3. Parsed transactions are normalized into internal transaction schema structures
4. Rules and payee logic are applied before persistence

### Scheduled Domain Automation

1. `celery-beat` schedules tasks from `backend/app/worker.py`
2. Celery worker executes domain tasks in `backend/app/tasks/*.py`
3. Tasks update recurring transactions, FX rates, connection sync state, and asset growth

## Architectural Conventions

- Backend routers map closely to domain entities (`accounts`, `transactions`, `budgets`, `assets`, `payees`, etc.)
- Services are mostly module-level functions rather than service classes
- Provider abstractions isolate vendor-specific behavior from routers
- Frontend uses one file per route page and a large shared API client instead of feature-scoped clients
- Lazy loading is used for route modules in `frontend/src/App.tsx`

## Important Entry Points

- Backend app startup: `backend/app/main.py`
- Backend worker startup: `backend/app/worker.py`
- Backend CLI entry: `backend/app/cli.py`
- Frontend bootstrap: `frontend/src/main.tsx`
- Frontend route graph: `frontend/src/App.tsx`
- Local orchestration: `docker-compose.yml`
- Production orchestration: `docker-compose.prod.yml`

## Notable Decisions / Tradeoffs

- The backend mixes synchronous-looking module-level services with async SQLAlchemy access, which keeps call sites simple but can create very large service modules
- Background processing is first-class; sync, recurring, asset, and FX flows are not handled only inline with user requests
- The frontend relies on `localStorage` token persistence instead of cookie-based auth
- Root-level compose is the primary developer and deployment interface; there is no Kubernetes/Terraform-style deployment layer in-repo
