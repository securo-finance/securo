# Integrations

## External Services

### PostgreSQL

- Primary application database.
- Default DSN is modeled in [`backend/app/core/config.py`](/workspaces/flux-pluggy/securo/backend/app/core/config.py) as `database_url`.
- Provisioned through [`docker-compose.yml`](/workspaces/flux-pluggy/securo/docker-compose.yml) and [`docker-compose.prod.yml`](/workspaces/flux-pluggy/securo/docker-compose.prod.yml).
- SQLAlchemy async engine/session are created in [`backend/app/core/database.py`](/workspaces/flux-pluggy/securo/backend/app/core/database.py).

### Redis / Celery

- Redis is used as both Celery broker and result backend.
- Configured in [`backend/app/worker.py`](/workspaces/flux-pluggy/securo/backend/app/worker.py) using `settings.redis_url`.
- Worker and beat services are launched in both compose files.
- Background jobs currently cover connection sync, recurring generation, asset growth, FX sync, and FX restamping.

### Pluggy

- Optional open-finance/bank-sync provider.
- Credentials: `PLUGGY_CLIENT_ID`, `PLUGGY_CLIENT_SECRET`, `PLUGGY_OAUTH_REDIRECT_URI` in [`.env.example`](/workspaces/flux-pluggy/securo/.env.example), [`backend/.env.example`](/workspaces/flux-pluggy/securo/backend/.env.example), and [`backend/app/core/config.py`](/workspaces/flux-pluggy/securo/backend/app/core/config.py).
- Provider implementation lives in [`backend/app/providers/pluggy.py`](/workspaces/flux-pluggy/securo/backend/app/providers/pluggy.py).
- Connection orchestration lives in [`backend/app/services/connection_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/connection_service.py).
- API surface is exposed from [`backend/app/api/connections.py`](/workspaces/flux-pluggy/securo/backend/app/api/connections.py).
- Frontend interaction points include [`frontend/src/components/bank-connect-dialog.tsx`](/workspaces/flux-pluggy/securo/frontend/src/components/bank-connect-dialog.tsx), [`frontend/src/components/connector-select-dialog.tsx`](/workspaces/flux-pluggy/securo/frontend/src/components/connector-select-dialog.tsx), and `connections.*` methods in [`frontend/src/lib/api.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/api.ts).
- Flow style: Pluggy widget/connect-token flow, not a pure redirect OAuth flow, even though the app exposes callback endpoints.

### Open Exchange Rates

- Optional FX-rate provider for multi-currency normalization.
- Credential: `OPENEXCHANGERATES_APP_ID` in README and [`backend/app/core/config.py`](/workspaces/flux-pluggy/securo/backend/app/core/config.py).
- Provider implementation: [`backend/app/providers/openexchangerates.py`](/workspaces/flux-pluggy/securo/backend/app/providers/openexchangerates.py).
- Related services/tasks: [`backend/app/services/fx_rate_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/fx_rate_service.py), [`backend/app/tasks/fx_rate_tasks.py`](/workspaces/flux-pluggy/securo/backend/app/tasks/fx_rate_tasks.py), [`backend/app/tasks/fx_backfill_tasks.py`](/workspaces/flux-pluggy/securo/backend/app/tasks/fx_backfill_tasks.py).

### Local File Storage

- Attachment storage defaults to local disk via `storage_provider=local`.
- Implementation: [`backend/app/providers/local_storage.py`](/workspaces/flux-pluggy/securo/backend/app/providers/local_storage.py).
- Runtime path: `storage_local_path`, defaulting to `./data/attachments` in config and `/app/data/attachments` in compose.
- Attachment domain logic lives in [`backend/app/services/attachment_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/attachment_service.py) and API routes in [`backend/app/api/attachments.py`](/workspaces/flux-pluggy/securo/backend/app/api/attachments.py).
- S3 settings exist in [`backend/app/core/config.py`](/workspaces/flux-pluggy/securo/backend/app/core/config.py), but no S3 provider implementation is present in the mapped code.

## Internal Integration Boundaries

### Backend API <-> Frontend SPA

- Frontend axios client is centralized in [`frontend/src/lib/api.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/api.ts) with `baseURL: '/api'`.
- JWT token is pulled from `localStorage` and sent on every request via axios interceptors.
- A 401 response clears the token and redirects the browser to `/login`.
- This assumes the frontend is reverse-proxied with the backend or served from a shared origin path layout.

### Auth

- JWT auth is implemented with `fastapi-users` in [`backend/app/core/auth.py`](/workspaces/flux-pluggy/securo/backend/app/core/auth.py).
- Frontend auth state is managed in [`frontend/src/contexts/auth-context.tsx`](/workspaces/flux-pluggy/securo/frontend/src/contexts/auth-context.tsx).
- Setup and registration flows also create initial domain state such as wallet/categories/rules via backend services.

### Background Jobs

- The FastAPI app triggers a startup sync dispatch in [`backend/app/main.py`](/workspaces/flux-pluggy/securo/backend/app/main.py).
- Celery beat schedules periodic tasks in [`backend/app/worker.py`](/workspaces/flux-pluggy/securo/backend/app/worker.py).
- Task modules then call service-layer async functions, usually by opening their own SQLAlchemy session.

## Environment And Deployment Dependencies

- Required/important variables are split across [`.env.example`](/workspaces/flux-pluggy/securo/.env.example), [`backend/.env.example`](/workspaces/flux-pluggy/securo/backend/.env.example), and compose files.
- Frontend container uses `BACKEND_URL` and `FRONTEND_URL` environment variables in compose files, but the TypeScript client does not read them directly; runtime handling likely depends on container/web-server configuration.
- Production images are built and pushed through [`.github/workflows/release.yml`](/workspaces/flux-pluggy/securo/.github/workflows/release.yml).

## Integration Gaps / Unclear Areas

- No explicit S3 provider implementation was found despite S3 configuration fields.
- No email provider, payment provider, or analytics integration was found.
- No webhook receiver surface was obvious in `backend/app/api`; sync appears pull-based through provider APIs and scheduled jobs.
