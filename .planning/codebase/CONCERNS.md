# Concerns

## Highest-Impact Risks

### Secrets Default To Insecure Values

Evidence:

- `backend/app/core/config.py` defaults `secret_key` to `change-me-in-production`
- `docker-compose.yml` defaults `SECRET_KEY` to `dev-secret-change-in-production`

Impact:

- Misconfigured deployments can run with guessable JWT signing keys
- This would compromise auth integrity immediately

Action:

- Fail startup outside explicit dev mode when insecure defaults are present
- Add a checked production env template

### JWT Stored In localStorage

Evidence:

- `frontend/src/lib/api.ts` reads `localStorage.getItem('token')`
- `frontend/src/contexts/auth-context.tsx` persists the access token in `localStorage`

Impact:

- Any XSS bug becomes credential exfiltration
- Session invalidation is entirely client-side unless JWT expiry is enforced tightly

Action:

- Decide whether this is an accepted tradeoff for self-hosted deployment
- If not, move to httpOnly cookies or shorten token lifetime and add refresh semantics

### Startup Sync Overlaps With Scheduled Sync

Evidence:

- `backend/app/main.py` dispatches `sync_all_connections` in lifespan startup
- `backend/app/worker.py` schedules `sync-all-connections-hourly`
- `backend/app/tasks/sync_tasks.py` separately scans for stale connections

Impact:

- Duplicate sync bursts on restarts
- Extra provider/API load and harder-to-reason operational behavior

Action:

- Pick one orchestration strategy and document it
- If startup sync stays, gate it with explicit env/config

## Data And Runtime Risks

### SQLite Test Harness Diverges From PostgreSQL Production

Evidence:

- runtime DB is PostgreSQL in `docker-compose.yml`
- tests use `sqlite+aiosqlite:///./test.db` in `backend/tests/conftest.py`

Impact:

- Postgres-specific behaviors around UUIDs, JSON, numeric precision, constraints, and query planning can slip through CI

Action:

- Keep SQLite for fast tests, but add a smaller PostgreSQL integration subset in CI

### Monolithic API Client Surface

Evidence:

- `frontend/src/lib/api.ts` holds a very large collection of domain calls in one file

Impact:

- Higher merge conflict rate
- Harder ownership and discoverability
- Easier for type drift or inconsistent conventions to accumulate

Action:

- Split by domain, for example `api/accounts.ts`, `api/transactions.ts`, `api/dashboard.ts`

### Large Route Components

Evidence:

- `frontend/src/pages/dashboard.tsx` mixes data fetching, transformations, local state, mutation wiring, and large render logic in one file

Impact:

- High regression surface
- Harder testing and slower feature iteration

Action:

- Extract view-model hooks and focused presentational sections before adding more dashboard behavior

## Integration And Provider Risks

### Missing Strong Production Config Story

Evidence:

- optional env vars are documented in `README.md`
- no tracked `.env.example` was found during this mapping pass
- S3 settings exist in `backend/app/core/config.py`, but no S3 provider implementation was found

Impact:

- Operators have to infer production-safe configuration
- Config surface can drift from real capabilities

Action:

- Add explicit env templates and mark not-yet-implemented settings clearly

### External Provider Failure Handling Is Broad

Evidence:

- startup sync in `backend/app/main.py` catches broad `Exception`
- `backend/app/api/connections.py` often converts generic exceptions into stringified HTTP errors

Impact:

- Operational failures may be harder to classify
- User-visible errors may be inconsistent or leak upstream messages

Action:

- Introduce narrower provider/service exception classes and structured error mapping

## Quality Risks

### No Automated Frontend Behavior Tests

Evidence:

- no frontend test files were found under `frontend/`
- CI in `.github/workflows/ci.yml` only runs lint and build for the frontend

Impact:

- UI regressions, auth flow issues, and route-level data bugs can ship undetected

Action:

- Add at least a thin set of route/dialog smoke tests around login, dashboard, transactions, and import flows

### Coverage Threshold May Hide Important Gaps

Evidence:

- `.github/workflows/ci.yml` enforces only `--cov-fail-under=60`

Impact:

- A passing badge does not imply strong regression protection, especially with a growing feature set

Action:

- Raise thresholds gradually and track domain-specific gaps instead of relying on one global floor

## Domain-Specific Fragility

### Multi-Currency Logic Is Spread Across Services

Evidence:

- `backend/app/services/dashboard_service.py` converts and aggregates primary amounts
- `backend/app/services/fx_rate_service.py` handles conversion/stamping
- transaction fields `amount_primary` and `fx_rate_used` live in `backend/app/models/transaction.py`

Impact:

- Subtle accounting inconsistencies are easy to introduce when adding features
- Historical rate semantics can drift between import/manual/recurring paths

Action:

- Centralize invariants for primary amount stamping and document the authoritative rules

### User Preferences Are Loosely Structured

Evidence:

- frontend and backend both read arbitrary keys from `user.preferences`
- examples in `backend/tests/conftest.py` and `frontend/src/components/app-layout.tsx`

Impact:

- Silent key drift across frontend/backend
- Harder migrations and weaker validation for settings-dependent features

Action:

- Introduce validated preference schemas or a typed contract shared across app boundaries

## Unclear / Needs Follow-Up

- I did not inspect every service and page file, so additional narrow concerns may exist in domain modules not sampled directly
- I did not verify whether unpublished runtime docs or deployment scripts outside tracked files cover some of the config gaps above
