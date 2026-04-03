# Conventions

## Backend Conventions

### Layering

Backend work generally follows this split:

- API handlers in `backend/app/api/`
- business logic in `backend/app/services/`
- persistence models in `backend/app/models/`
- schema validation/serialization in `backend/app/schemas/`
- provider abstractions in `backend/app/providers/`

This separation is consistent enough that new backend work should usually follow the same pattern.

### Typing And Models

- SQLAlchemy 2 typed declarative style is used, for example `backend/app/models/transaction.py`
- Pydantic v2 style schemas are used under `backend/app/schemas/`
- UUIDs are the standard entity identifier type across the backend
- Money values are usually `Decimal` in backend domain/model code

### Async Style

- API handlers and services are predominantly `async`
- DB access uses `AsyncSession`
- External HTTP calls use `httpx.AsyncClient`
- Celery tasks bridge to async code with `asyncio.run(...)`

### Error Handling

- Route handlers usually convert service exceptions into `HTTPException`, for example `backend/app/api/connections.py`
- Service-layer errors appear to use plain `ValueError` or generic exceptions rather than custom domain exception types
- Some broad exception handling logs and continues, for example startup sync in `backend/app/main.py`

### Configuration

- Runtime configuration is centralized in `backend/app/core/config.py`
- Settings are cached through `get_settings()` with `lru_cache`
- Env var names follow uppercase snake case

## Frontend Conventions

### Component Organization

- Route-level screens live in `frontend/src/pages/`
- Shared feature components live in `frontend/src/components/`
- primitive reusable UI parts live in `frontend/src/components/ui/`
- common helpers live in `frontend/src/lib/`, `frontend/src/hooks/`, and `frontend/src/contexts/`

### Data Fetching

- API access is centralized in `frontend/src/lib/api.ts`
- Components/pages typically call that client through React Query hooks
- Query keys are simple arrays of strings and parameters, for example in `frontend/src/pages/dashboard.tsx`
- Mutations commonly invalidate broad query prefixes instead of updating cache surgically

### Styling

- Utility-first styling with Tailwind classes is used throughout `frontend/src/`
- UI primitives follow shadcn/Radix composition patterns
- Iconography is mostly from `lucide-react`

### State Management

- Local component state with `useState` is common
- App-wide auth state uses React context in `frontend/src/contexts/auth-context.tsx`
- Server state uses React Query
- There is no Redux/Zustand/etc. in the tracked source

## Naming Patterns

- Backend modules use snake_case filenames
- Frontend page and component filenames lean toward kebab-case
- Query/mutation helpers in `frontend/src/lib/api.ts` are grouped by domain object
- Services often expose verb-centric functions such as `get_summary`, `sync_connection`, `apply_rules_to_transaction`

## Linting And Formatting

- Ruff line length is 100 in `backend/pyproject.toml`
- Ruff ignores `E711` and `E712`
- Frontend linting is ESLint flat config in `frontend/eslint.config.js`
- No dedicated formatter config file (for example Prettier or Black) was found during this pass

## Test Style

- Backend tests use `pytest` + `pytest-asyncio`
- API tests use `httpx.AsyncClient` against the in-process ASGI app from `backend/tests/conftest.py`
- Tests rely on fixture composition heavily
- External services are mocked with `unittest.mock.patch` / `AsyncMock`

## Recurring Implementation Idioms

- Domain routers, service files, schema files, and frontend pages often share the same business noun
- Comments are sparse and usually reserved for non-obvious behavior
- Business rules are often encoded directly in service functions rather than separate policy objects
- User preferences are stored as flexible dictionaries and read in frontend/backend code paths

## Deviations And Mixed Patterns

- React code uses `useCallback` and `useMemo` in places such as `frontend/src/contexts/auth-context.tsx` and `frontend/src/pages/dashboard.tsx`, but usage is not uniformly minimal
- Some frontend route modules are very large and combine data, transformations, and rendering in a single file
- Some backend routes return schema models consistently, while others return ad hoc dicts with extra fields
