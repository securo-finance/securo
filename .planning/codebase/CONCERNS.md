# Concerns

## Primary Risks

### Large Backend Service Modules

- Several domain services are sizeable and handle multiple responsibilities, for example `backend/app/services/transaction_service.py` and `backend/app/services/import_service.py`
- Risk: changes become harder to reason about, and coupling between query logic, domain rules, and serialization-side concerns increases over time

### Uneven Test Coverage Surface

- Backend testing is strong, but key runtime areas are excluded from coverage:
  - `backend/app/tasks/*`
  - `backend/app/providers/pluggy.py`
  - `backend/app/worker.py`
- Frontend has no automated test suite in the current tree
- Risk: regressions in async scheduling, third-party integrations, and UI flows are caught late

### Auth Token Storage

- Frontend auth tokens are stored in `localStorage` in `frontend/src/lib/api.ts` and `frontend/src/contexts/auth-context.tsx`
- Risk: XSS exposure is higher than cookie/session-based approaches; any future rich HTML input should be reviewed carefully

### Startup Side Effects

- Backend startup in `backend/app/main.py` dispatches a sync-all-connections Celery task immediately
- Risk: container restarts can create noisy sync traffic or confusing behavior during development/recovery if Redis/Celery is unhealthy

### SQLite vs PostgreSQL Test Mismatch

- Tests run on SQLite in `backend/tests/conftest.py`, while production runs on PostgreSQL
- Risk: async ORM behavior is covered, but Postgres-specific SQL/constraint/index behavior can still diverge

## Secondary Risks

### Frontend API Client Size

- `frontend/src/lib/api.ts` is a large, central API wrapper covering many domains
- Risk: merge conflicts and weak feature isolation as the surface area keeps growing

### Flat Frontend Growth

- `frontend/src/pages/` and `frontend/src/components/` are still manageable, but the app is already feature-dense
- Risk: discoverability and ownership will degrade without feature-level grouping if the UI grows much further

### Configuration Drift

- Integration/config defaults are spread across:
  - `.env.example`
  - `backend/.env.example`
  - `backend/app/core/config.py`
  - compose files
- Risk: env documentation can drift from real runtime expectations

### Incomplete Storage Abstraction Rollout

- `backend/app/core/config.py` contains S3 settings, but no active S3 provider implementation was found
- Risk: the settings surface implies a capability that is not actually available yet

## Fragile Areas To Treat Carefully

- Connection sync and reconnect flows in `backend/app/api/connections.py` and `backend/app/services/connection_service.py`
- Import parsing and normalization in `backend/app/services/import_service.py`
- FX stamping and transfer logic in `backend/app/services/fx_rate_service.py` and `backend/app/services/transaction_service.py`
- Attachment handling across API/service/provider boundaries
- App shell behavior in `frontend/src/components/app-layout.tsx`, which combines navigation, privacy mode, backup UI, onboarding, account summaries, and user actions

## Security / Operational Notes

- The root `.env.example` still shows a development-style secret placeholder
- Compose defaults include permissive dev secrets and local URLs; production deployments need explicit override discipline
- Optional external providers should be reviewed for timeout/retry/error-shaping behavior before scaling usage

## Recommended Follow-Up Mapping Targets

- Review `backend/app/services/connection_service.py` and task modules for sync failure/retry semantics
- Audit whether backup/export behavior referenced by the frontend is fully covered and documented
- Decide whether frontend feature modules should be reorganized before more pages/components are added
- Revisit auth storage strategy if the app grows richer browser-side content or embeds third-party UI
