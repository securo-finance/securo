# Integrations

## External Systems

The codebase integrates with a small set of external systems:

- PostgreSQL for primary data storage via `DATABASE_URL`
- Redis for Celery broker/result backend via `REDIS_URL`
- Pluggy for bank connectivity in `backend/app/providers/pluggy.py`
- Open Exchange Rates for FX data in `backend/app/providers/openexchangerates.py`
- Local filesystem storage for attachments in `backend/app/providers/local_storage.py`

No evidence of third-party analytics, hosted auth, or SaaS observability was found in the tracked source.

## Database

- Runtime database URL comes from `backend/app/core/config.py`
- Async engine/session are created in `backend/app/core/database.py`
- Migrations are managed through `backend/alembic/env.py` and `backend/alembic/versions/`
- Compose service `db` in `docker-compose.yml` runs PostgreSQL 16

## Authentication

- Authentication is JWT-based through FastAPI Users in `backend/app/core/auth.py`
- Login/register/reset/user routes are mounted in `backend/app/main.py`
- Frontend stores the bearer token in `localStorage` and injects it in `frontend/src/lib/api.ts`
- `frontend/src/contexts/auth-context.tsx` hydrates current user state by calling `/api/users/me`

## Bank Connectivity

### Pluggy

- Provider implementation: `backend/app/providers/pluggy.py`
- Connect flow is widget-based, not redirect OAuth
- Frontend dependency: `react-pluggy-connect` in `frontend/package.json`
- Backend routes:
  - `POST /api/connections/connect-token`
  - `POST /api/connections/oauth/callback`
  - `POST /api/connections/{id}/reconnect-token`
  - `POST /api/connections/{id}/sync`
  - implemented in `backend/app/api/connections.py`
- Provider metadata registry appears in `backend/app/providers/__init__.py`

### Connection Synchronization

- Sync orchestration is in `backend/app/services/connection_service.py`
- Background connection sync jobs run in `backend/app/tasks/sync_tasks.py`
- App startup also dispatches `sync_all_connections` from the lifespan hook in `backend/app/main.py`

## FX And Currency Data

- FX provider implementation: `backend/app/providers/openexchangerates.py`
- FX conversion/stamping logic: `backend/app/services/fx_rate_service.py`
- Scheduled tasks: `backend/app/tasks/fx_rate_tasks.py`, `backend/app/tasks/fx_backfill_tasks.py`
- Configuration keys:
  - `OPENEXCHANGERATES_APP_ID`
  - `FX_SYNC_MODE`
  - `SUPPORTED_CURRENCIES`
  - all declared in `backend/app/core/config.py`

## File And Attachment Storage

- Storage abstraction in `backend/app/providers/storage.py` and `backend/app/providers/base.py`
- Local implementation in `backend/app/providers/local_storage.py`
- Attachment service layer in `backend/app/services/attachment_service.py`
- Attachment API in `backend/app/api/attachments.py`
- Default path is `./data/attachments` in `backend/app/core/config.py`
- Compose maps this to a named Docker volume in `docker-compose.yml`

## Import Formats

Inbound file parsing is implemented locally rather than through external services:

- OFX parsing via `ofxparse` in `backend/app/services/import_service.py`
- QIF parsing in `backend/app/services/import_service.py`
- CAMT.053 XML parsing in `backend/app/services/import_service.py`
- CSV parsing with configurable date/amount handling in `backend/app/services/import_service.py`

## Frontend-To-Backend Contract

- Frontend calls the backend through a single Axios client in `frontend/src/lib/api.ts`
- Vite dev proxy forwards `/api` to `BACKEND_URL` in `frontend/vite.config.ts`
- Production/reverse proxy details are partly handled by `frontend/nginx.conf`

## CI And Hosted Integrations

- GitHub Actions CI is defined in `.github/workflows/ci.yml`
- Coverage badge updates use `schneegans/dynamic-badges-action` with a GitHub Gist target in `.github/workflows/ci.yml`

## Environment-Driven Behavior

Important integration toggles come from env vars in `backend/app/core/config.py` and `docker-compose.yml`:

- `DATABASE_URL`
- `REDIS_URL`
- `FRONTEND_URL`
- `PLUGGY_CLIENT_ID`
- `PLUGGY_CLIENT_SECRET`
- `PLUGGY_OAUTH_REDIRECT_URI`
- `OPENEXCHANGERATES_APP_ID`
- `STORAGE_PROVIDER`
- `STORAGE_LOCAL_PATH`

## Gaps / Unclear Areas

- S3 storage fields exist in `backend/app/core/config.py`, but no tracked S3 provider implementation was found
- No outbound email provider, webhook sender, or audit-log sink was found in tracked source
- No `.env.example` was found in the root or `backend/` during this mapping pass
