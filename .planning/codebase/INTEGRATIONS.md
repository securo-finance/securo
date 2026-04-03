# Integrations

## External Services

### PostgreSQL

- Purpose: primary relational datastore for users, accounts, transactions, rules, assets, budgets, and sync metadata
- Configuration: `DATABASE_URL` in `backend/app/core/config.py`
- Runtime wiring:
  - `docker-compose.yml`
  - `docker-compose.prod.yml`
  - `backend/app/core/database.py`
- Schema evolution: Alembic migrations in `backend/alembic/versions/`

### Redis

- Purpose: Celery broker and result backend
- Configuration: `REDIS_URL` in `backend/app/core/config.py`
- Runtime wiring:
  - `docker-compose.yml`
  - `docker-compose.prod.yml`
  - `backend/app/worker.py`

### Pluggy

- Purpose: optional open-finance/bank connection provider
- Credentials:
  - `PLUGGY_CLIENT_ID`
  - `PLUGGY_CLIENT_SECRET`
  - `PLUGGY_OAUTH_REDIRECT_URI`
- Backend integration points:
  - provider implementation in `backend/app/providers/pluggy.py`
  - provider registry in `backend/app/providers/__init__.py`
  - API endpoints in `backend/app/api/connections.py`
  - orchestration in `backend/app/services/connection_service.py`
- Frontend integration points:
  - Pluggy widget dependency in `frontend/package.json`
  - bank connection UI in `frontend/src/components/bank-connect-dialog.tsx`
  - provider selection/reconnect settings in `frontend/src/components/connector-select-dialog.tsx` and `frontend/src/components/connection-settings-dialog.tsx`
- Notes:
  - Backend creates connect tokens rather than redirecting for the primary Pluggy flow
  - App startup dispatches a full stale-connection sync from `backend/app/main.py`

### Open Exchange Rates

- Purpose: optional FX rates for cross-currency transactions and recurring data restamping
- Credential: `OPENEXCHANGERATES_APP_ID`
- Backend integration points:
  - provider implementation in `backend/app/providers/openexchangerates.py`
  - FX services/tasks in `backend/app/services/fx_rate_service.py` and `backend/app/tasks/fx_rate_tasks.py`
  - API endpoints in `backend/app/api/fx_rates.py`
- Runtime behavior:
  - on-demand conversion support
  - scheduled sync controlled by `FX_SYNC_MODE`

## Internal Service Boundaries

### Frontend to Backend

- Frontend sends all application requests to `/api` using Axios in `frontend/src/lib/api.ts`
- Vite dev server proxies `/api` to `BACKEND_URL` in `frontend/vite.config.ts`
- Production frontend container points to backend via container DNS in `docker-compose.prod.yml`

### Background Jobs

- Celery worker and beat run alongside the API in both compose files
- Scheduled tasks registered in `backend/app/worker.py`:
  - bank sync
  - recurring generation
  - asset growth application
  - FX sync
  - recurring FX restamping

## Storage

### Local Attachment Storage

- Current provider: local filesystem
- Config:
  - `STORAGE_PROVIDER`
  - `STORAGE_LOCAL_PATH`
  - attachment size/extension limits in `backend/app/core/config.py`
- Code locations:
  - provider abstraction in `backend/app/providers/storage.py`
  - local implementation in `backend/app/providers/local_storage.py`
  - attachment API in `backend/app/api/attachments.py`
  - attachment service in `backend/app/services/attachment_service.py`
- Container persistence: `attachments` volume in both compose files

### S3 Placeholder

- S3 configuration fields exist in `backend/app/core/config.py`
- No concrete S3 provider implementation is present in the mapped tree, so this looks scaffolded rather than active

## Authentication Integration

- User auth is handled inside the backend with `fastapi-users`; there is no external IdP integration in the current tree
- Frontend stores bearer tokens in `localStorage` and injects them through Axios interceptors in `frontend/src/lib/api.ts`
- Auth bootstrap and session management live in `frontend/src/contexts/auth-context.tsx`

## Import/Export Interfaces

- Supported import formats implemented in `backend/app/services/import_service.py`:
  - OFX
  - QIF
  - CAMT.053 XML
  - CSV
- Transaction export endpoint in `backend/app/api/transactions.py`
- Additional backup/export capability is implied by frontend usage of `backupApi` in `frontend/src/components/app-layout.tsx`; verify corresponding backend API details when changing backup flows

## Environment Dependencies

- Root `.env.example` documents Pluggy variables only
- `backend/.env.example` documents core backend variables
- Compose files add production/dev defaults for database, Redis, storage, frontend URL, and optional provider credentials

## Gaps / Cautions

- Third-party integrations are optional and degrade to local/manual behavior when credentials are absent
- FX conversion has a documented 1:1 fallback behavior surfaced in the README and transaction API tagging logic in `backend/app/api/transactions.py`
- Webhook-based integrations were not found in the current repository tree
