# Conventions

## Backend Conventions

### Module Organization

- Routers live in [`backend/app/api`](/workspaces/flux-pluggy/securo/backend/app/api), services in [`backend/app/services`](/workspaces/flux-pluggy/securo/backend/app/services), models in [`backend/app/models`](/workspaces/flux-pluggy/securo/backend/app/models), and schemas in [`backend/app/schemas`](/workspaces/flux-pluggy/securo/backend/app/schemas).
- Business logic is generally function-based, not class-based. Service functions are imported directly where needed.
- API modules define `router = APIRouter(...)` near the top and expose one route group per file.

### Query / Persistence Style

- Async SQLAlchemy sessions are injected with `Depends(get_async_session)` from [`backend/app/core/database.py`](/workspaces/flux-pluggy/securo/backend/app/core/database.py).
- Queries are usually written with SQLAlchemy `select(...)` expressions and `session.execute(...)`.
- Relationship loading often uses `selectinload(...)`, for example in [`backend/app/services/transaction_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/transaction_service.py).
- Service functions usually call `await session.commit()` themselves after mutations rather than relying on a unit-of-work wrapper.

### Error Handling

- Service-layer validation failures commonly raise `ValueError`.
- Routers translate those to `HTTPException(status_code=400, ...)`, as in [`backend/app/api/transactions.py`](/workspaces/flux-pluggy/securo/backend/app/api/transactions.py).
- Missing resources are typically handled by returning `None` from services and converting that to `404` at the router boundary.
- Background tasks log exceptions rather than failing hard, for example [`backend/app/tasks/sync_tasks.py`](/workspaces/flux-pluggy/securo/backend/app/tasks/sync_tasks.py).

### Domain Patterns

- Ownership checks are embedded directly in query filters, usually by comparing `user_id` or joined `BankConnection.user_id`.
- Enrichment steps are explicit and imperative: after creating/syncing transactions, code may apply rules, derive payees, stamp FX fields, and detect transfer pairs.
- Multi-currency fields such as `amount_primary` and `fx_rate_used` are first-class domain attributes across services, dashboarding, and exports.
- Provider lookups are registry-based through [`backend/app/providers/__init__.py`](/workspaces/flux-pluggy/securo/backend/app/providers/__init__.py).

### Style Tooling

- Ruff is the backend linter, configured in [`backend/pyproject.toml`](/workspaces/flux-pluggy/securo/backend/pyproject.toml).
- The codebase uses type hints broadly, including `uuid.UUID`, `Optional[...]`, and return annotations.
- Imports and formatting appear close to default Ruff/PEP 8 expectations with a 100-column limit.

## Frontend Conventions

### Component And Route Structure

- Route pages default-export a component from files in [`frontend/src/pages`](/workspaces/flux-pluggy/securo/frontend/src/pages).
- Shared components use named exports, for example [`frontend/src/components/page-header.tsx`](/workspaces/flux-pluggy/securo/frontend/src/components/page-header.tsx).
- UI primitives under [`frontend/src/components/ui`](/workspaces/flux-pluggy/securo/frontend/src/components/ui) follow shadcn-style wrappers around lower-level libraries.

### State Management

- Remote/server state is primarily managed with TanStack Query.
- Auth is managed through React context in [`frontend/src/contexts/auth-context.tsx`](/workspaces/flux-pluggy/securo/frontend/src/contexts/auth-context.tsx).
- Local interaction state remains inside page/dialog components via `useState`, `useEffect`, and occasionally `useMemo`.
- No separate global state store such as Redux or Zustand was found.

### Data Access

- All backend requests are funneled through [`frontend/src/lib/api.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/api.ts).
- That file exports grouped API namespaces (`auth`, `accounts`, `transactions`, etc.) matching backend domains.
- Token handling is centralized in axios interceptors rather than repeated inside components.

### Styling

- Styling is utility-first through Tailwind classes.
- Design tokens are defined as CSS variables in [`frontend/src/index.css`](/workspaces/flux-pluggy/securo/frontend/src/index.css).
- Components frequently compose long class strings directly inline.
- Icons are mainly `lucide-react`, with category-specific helpers in [`frontend/src/lib/category-icons.ts`](/workspaces/flux-pluggy/securo/frontend/src/lib/category-icons.ts).

### Lint / Language

- ESLint flat config in [`frontend/eslint.config.js`](/workspaces/flux-pluggy/securo/frontend/eslint.config.js) covers TS, React hooks, and Vite refresh rules.
- The frontend uses path aliases like `@/components/...`; the actual alias mapping lives in Vite/TS config files under [`frontend/vite.config.ts`](/workspaces/flux-pluggy/securo/frontend/vite.config.ts) and [`frontend/tsconfig.json`](/workspaces/flux-pluggy/securo/frontend/tsconfig.json).
- Files are mostly TypeScript-first with explicit `type` imports where useful.

## Recurring Idioms

- Backend services often `flush()` before running follow-up logic that needs generated IDs.
- Routers frequently post-process ORM objects into response schemas with `model_validate(..., from_attributes=True)`.
- Frontend pages often colocate query definitions, mutation definitions, helper formatters, and rendering in one file.
- Queries use descriptive array keys such as `['dashboard', 'summary', selectedMonth]`.

## Inconsistencies / Noteworthy Deviations

- Some frontend files still use `useCallback`/`useMemo` patterns manually, while others are simpler; there is no obvious repo-wide React compiler guidance encoded in linting.
- The frontend installs form/validation packages broadly, but usage is not uniform across pages.
- Naming mixes English code structure with some Portuguese domain strings/test fixtures, which is intentional but worth expecting during implementation work.
