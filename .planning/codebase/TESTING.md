# Testing

## Current Test Surface

Testing is backend-heavy. The repo has a substantial Python test suite under [`backend/tests`](/workspaces/flux-pluggy/securo/backend/tests) and no dedicated frontend unit or E2E test setup in [`frontend/package.json`](/workspaces/flux-pluggy/securo/frontend/package.json).

## Backend Test Stack

- Test runner: pytest from [`backend/pyproject.toml`](/workspaces/flux-pluggy/securo/backend/pyproject.toml).
- Async support: `pytest-asyncio`.
- Coverage: `pytest-cov`.
- HTTP/API testing: `httpx.AsyncClient` + `ASGITransport` in [`backend/tests/conftest.py`](/workspaces/flux-pluggy/securo/backend/tests/conftest.py).
- Test database: SQLite via `sqlite+aiosqlite:///./test.db` in [`backend/tests/conftest.py`](/workspaces/flux-pluggy/securo/backend/tests/conftest.py).
- DB setup strategy: create all tables from SQLAlchemy metadata once per session, then clean tables between tests.

## Backend Test Structure

- Shared fixtures and app dependency overrides live in [`backend/tests/conftest.py`](/workspaces/flux-pluggy/securo/backend/tests/conftest.py).
- Tests are organized mostly by domain/service/API file, for example:
  - [`backend/tests/test_transactions_api.py`](/workspaces/flux-pluggy/securo/backend/tests/test_transactions_api.py)
  - [`backend/tests/test_transaction_service.py`](/workspaces/flux-pluggy/securo/backend/tests/test_transaction_service.py)
  - [`backend/tests/test_fx_rates.py`](/workspaces/flux-pluggy/securo/backend/tests/test_fx_rates.py)
  - [`backend/tests/test_connection_service.py`](/workspaces/flux-pluggy/securo/backend/tests/test_connection_service.py)
  - [`backend/tests/test_assets_api.py`](/workspaces/flux-pluggy/securo/backend/tests/test_assets_api.py)
- Coverage spans API endpoints, service logic, and provider-related behavior.

## Mocking / Isolation Patterns

- External behavior is mocked with `unittest.mock.patch` / `AsyncMock`, visible in [`backend/tests/conftest.py`](/workspaces/flux-pluggy/securo/backend/tests/conftest.py) and domain-specific test files.
- The application’s `get_async_session` dependency is overridden globally for tests, keeping requests in-process and isolated from the real PostgreSQL database.
- SQLite is used instead of Postgres in tests, so behavior that depends on Postgres-specific SQL or JSON/UUID nuances may not be perfectly mirrored.

## Commands And CI Gates

- Local backend test command from README: `docker compose exec backend pytest`.
- CI backend checks in [`.github/workflows/ci.yml`](/workspaces/flux-pluggy/securo/.github/workflows/ci.yml):
  - `ruff check .`
  - `pytest --cov=app --cov-report=xml --cov-report=term-missing --cov-fail-under=60`
- CI frontend checks in [`.github/workflows/ci.yml`](/workspaces/flux-pluggy/securo/.github/workflows/ci.yml):
  - `npm ci`
  - `npm run lint`
  - `npm run build`

## Frontend Testing Posture

- No `test` script is defined in [`frontend/package.json`](/workspaces/flux-pluggy/securo/frontend/package.json).
- No Vitest/Jest/Playwright/Cypress configuration files were found at repo root or under [`frontend`](/workspaces/flux-pluggy/securo/frontend).
- Frontend quality is currently enforced by linting and successful TypeScript/Vite build rather than automated UI tests.

## Gaps And Risks

- The backend test suite omits worker/task modules and the Pluggy provider from coverage on purpose in [`backend/pyproject.toml`](/workspaces/flux-pluggy/securo/backend/pyproject.toml).
- The lack of frontend tests leaves complex pages and interaction-heavy dialogs unprotected against regressions.
- SQLite-backed tests may miss asyncpg/PostgreSQL-specific behavior, migration drift, and some production query characteristics.
- There is no obvious end-to-end test path that validates frontend/backend integration through the browser.
