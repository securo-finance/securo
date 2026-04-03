# Testing

## Current Test Stack

- `pytest` configured in `backend/pyproject.toml`
- `pytest-asyncio` for async tests
- `pytest-cov` for coverage reporting
- `httpx` ASGI transport for API-level integration tests
- SQLite test database in `backend/tests/conftest.py`

## Test Layout

Backend tests are concentrated in `backend/tests/` with domain-oriented files such as:

- `backend/tests/test_accounts_api.py`
- `backend/tests/test_transactions_api.py`
- `backend/tests/test_dashboard_service.py`
- `backend/tests/test_import_service.py`
- `backend/tests/test_rule_engine.py`
- `backend/tests/test_connection_service.py`
- `backend/tests/test_fx_rates.py`
- `backend/tests/test_auth_api.py`

No frontend unit/component/e2e test directory was found in `frontend/` during this mapping pass.

## Test Harness

- `backend/tests/conftest.py` creates a SQLite database and overrides `get_async_session`
- The FastAPI app is exercised in-process through `AsyncClient(transport=ASGITransport(app=app))`
- Shared fixtures create authenticated users, accounts, categories, rules, and transactions
- Test DB teardown removes `./test.db` at session end

## What Is Covered Well

- API route behavior across major finance domains
- Core service behavior for dashboards, budgets, imports, payees, accounts, rules, recurring, assets, and FX
- Auth flows
- Import parsers and transfer detection logic

## Notable Gaps

- No tracked frontend tests for routes, forms, or UI regressions
- No obvious end-to-end browser automation
- Background Celery task behavior appears to be covered indirectly at best
- Production-specific infrastructure behavior from Docker/Nginx is not represented as tests
- Multi-process/runtime integration between frontend, backend, Redis, and PostgreSQL is not verified in CI

## CI Enforcement

GitHub Actions in `.github/workflows/ci.yml` runs:

- backend Ruff lint
- backend pytest with coverage and `--cov-fail-under=60`
- frontend ESLint
- frontend typecheck/build through `npm run build`

This gives decent backend confidence but little automated protection for frontend behavior.

## Mocking Patterns

- `unittest.mock.patch` and `AsyncMock` are imported in `backend/tests/conftest.py`
- External provider/network behavior is mocked rather than using live integration environments
- SQLite stands in for PostgreSQL, which is fast but can miss dialect-specific issues

## Commands

Common test/check commands inferred from repo config:

- `cd backend && pip install -e ".[dev]" && pytest`
- `cd backend && ruff check .`
- `cd frontend && npm ci && npm run lint`
- `cd frontend && npm run build`

## Risk Assessment

- Backend coverage exists and is enforced, but the threshold is modest
- Frontend regressions are likely to be caught late because only lint/type/build gates exist
- DB behavior differences between SQLite and PostgreSQL remain a standing risk, especially around UUIDs, numeric handling, and query semantics
