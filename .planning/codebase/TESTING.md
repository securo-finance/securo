# Testing

## Current Test Setup

### Backend

- Test framework: Pytest with `pytest-asyncio`
- Test location: `backend/tests/`
- App-under-test: real FastAPI app from `backend/app/main.py`
- HTTP client: `httpx.AsyncClient` with `ASGITransport` in `backend/tests/conftest.py`
- Test database: SQLite via `sqlite+aiosqlite:///./test.db` in `backend/tests/conftest.py`
- Table lifecycle: created once per session and dropped at teardown in `backend/tests/conftest.py`
- Dependency override: `get_async_session` is replaced in tests to route API calls into the SQLite test DB

### Frontend

- No dedicated frontend unit/integration test runner or test files were found
- Frontend quality gates are lint and build only:
  - `npm run lint`
  - `npm run build`

## Coverage and CI

- Coverage tooling is configured in `backend/pyproject.toml`
- CI runs backend tests with:
  - `pytest --cov=app --cov-report=xml --cov-report=term-missing --cov-fail-under=60`
- Coverage XML is uploaded as an artifact and used to update a badge in `.github/workflows/ci.yml`
- Coverage excludes:
  - `app/cli.py`
  - `app/worker.py`
  - `app/tasks/*`
  - `app/providers/pluggy.py`

## Backend Test Organization

- `backend/tests/conftest.py` centralizes shared fixtures:
  - event loop
  - database/session setup
  - API client
  - auth token
  - common domain fixtures such as users, categories, accounts, rules, and transactions
- Test files are mostly feature/domain aligned, for example:
  - `backend/tests/test_transactions_api.py`
  - `backend/tests/test_transaction_service.py`
  - `backend/tests/test_connection_service.py`
  - `backend/tests/test_dashboard_service.py`
  - `backend/tests/test_fx_rates.py`

## Testing Style

- Tests are predominantly integration-style at the API or service boundary
- Assertions usually validate HTTP status, serialized response shape, and important side effects
- Regression-oriented tests are explicitly documented in several files, for example transaction update/date handling in `backend/tests/test_transactions_api.py`
- Mocking is selective rather than pervasive; `unittest.mock` is available in `backend/tests/conftest.py` for provider/task isolation when needed

## Commands

- Backend local test run from README guidance:
  - `docker compose exec backend pytest`
- CI/backend package install:
  - `pip install -e ".[dev]"`
- Frontend checks:
  - `npm ci`
  - `npm run lint`
  - `npm run build`

## Notable Gaps

- No frontend automated tests were found
- Celery task behavior is largely outside direct coverage because task files are omitted from coverage
- Pluggy provider code is also omitted from coverage, so external-integration regressions may rely more on manual verification
- SQLite-backed tests are fast and isolated, but they may not expose PostgreSQL-specific behavior differences
