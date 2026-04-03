# Conventions

## Backend Conventions

### Structure

- FastAPI routers stay thin and delegate to service modules, for example `backend/app/api/transactions.py`
- Business logic is primarily written as module-level async functions in `backend/app/services/*.py`
- Models, schemas, routers, and services use aligned domain names (`account`, `transaction`, `budget`, `payee`, etc.)

### Style

- Python style is close to Ruff defaults with a configured line length of 100 in `backend/pyproject.toml`
- Typing is used broadly across service and API functions
- Pydantic schema names follow `Create`, `Update`, `Read`, and specific request/response suffix patterns
- Inline comments are used sparingly and usually explain domain-specific behavior rather than syntax

### Error Handling

- Services generally raise `ValueError` for domain/business validation failures
- Routers translate those exceptions into HTTP errors with explicit status codes
- Unexpected provider/sync failures are often caught and wrapped with generic 4xx/5xx messages in router handlers

### Data and Ownership Rules

- Backend service queries consistently enforce user ownership boundaries before returning or mutating records
- Cross-cutting transaction concerns include:
  - transfer pairing
  - primary currency stamping
  - rule-based categorization
  - attachment counts
  - payee enrichment

### Patterns Worth Preserving

- Use `Depends(get_async_session)` and `Depends(current_active_user)` in routers
- Keep provider-specific code inside `backend/app/providers/`
- Use Alembic revisions for schema changes instead of ad hoc table creation logic
- Prefer extending existing domain services before adding duplicate business logic to routers

## Frontend Conventions

### Structure

- Route-level pages live in `frontend/src/pages/`
- Shared feature components live in `frontend/src/components/`
- Reusable primitives live in `frontend/src/components/ui/`
- Common backend access goes through `frontend/src/lib/api.ts`

### State and Data Flow

- Server state is handled with TanStack Query
- Auth state is held in `frontend/src/contexts/auth-context.tsx`
- Browser persistence currently relies on `localStorage` for tokens and onboarding/privacy flags
- Pages and components tend to call API wrapper functions directly rather than through a separate service layer

### Styling

- Tailwind utility classes are used directly in component JSX
- UI primitives and styling patterns are consistent with a shadcn/Radix-style component approach
- Iconography is standardized on `lucide-react`

### TypeScript / React Patterns

- Functional components only; no class components found
- Lazy route imports in `frontend/src/App.tsx`
- Hooks are used for auth, queries, translation, theme, and local UI state
- `useCallback` appears in some shared components/contexts, but there is no elaborate memoization framework

## Testing and Quality Conventions

- Backend tests use async pytest functions marked with `@pytest.mark.asyncio`
- API tests interact with the actual ASGI app through HTTPX instead of mocking FastAPI routes directly
- Fixtures in `backend/tests/conftest.py` create realistic seeded data and auth tokens
- CI enforces:
  - `ruff check .` in `backend/`
  - backend coverage threshold of 60%
  - `npm run lint`
  - `npm run build`

## Naming Conventions

- Backend service files: `*_service.py`
- Backend task files: `*_tasks.py`
- Frontend component files: kebab-case
- Frontend imports commonly use the `@/` alias
- API route prefixes align with plural resource names such as `/api/transactions` and `/api/connections`

## Local Practices Implied By The Tree

- Compose-first development is the default path in `README.md`
- Optional integrations are expected to be disabled cleanly when env vars are absent
- The project has strong regression-test habits on backend API and service behavior, but frontend behavior is mostly protected by lint/type/build checks rather than tests
