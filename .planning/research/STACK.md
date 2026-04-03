# Stack Research

**Domain:** Brownfield self-hosted personal finance web app v2 (rebrand + category-budget migration)
**Researched:** 2026-04-03
**Confidence:** HIGH

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| React | 19.2.x existing major | SPA UI for categories, dashboard, and rebrand updates | Keep the current SPA. This v2 is a domain migration, not a rendering-model rewrite. React 19 fits the existing app and avoids churn. Confidence: HIGH |
| React Router | 7.13.x existing major | Route-level migration of `/categories`, `/budgets`, nav, and redirects | Keep the current router and use route-by-route UI replacement. Do not adopt new framework mode for this v2. Confidence: HIGH |
| TanStack Query | 5.90.x existing major | Server-state reads/mutations for categories, dashboards, and legacy-to-new invalidation | Query 5 already matches the app shape. It is sufficient for dual-read cutover and targeted cache invalidation without adding client-state tooling. Confidence: HIGH |
| FastAPI | 0.109+ existing line | HTTP API for category-owned budget writes and compatibility reads | Keep FastAPI and extend the existing router/service/schema pattern. This is the lowest-risk way to evolve a finance domain model in-place. Confidence: HIGH |
| SQLAlchemy 2 async + Alembic | SQLAlchemy 2.x, Alembic 1.13+ existing line | Schema migration, data backfill, and compatibility queries | This migration is mostly relational and data-shaping work. Alembic + SQLAlchemy are already in place and are the correct tools for a brownfield budget-model cutover. Confidence: HIGH |
| PostgreSQL | 16 existing | Source of truth for categories and budget state | Keep Postgres as the only persistent source of truth. Budget ownership moving into categories is a schema migration, not an infra change. Confidence: HIGH |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `react-hook-form` + `zod` | 7.71.x + 4.3.x existing | Single category create/edit form that includes optional budget controls | Use for the new category dialog/page so budget fields stay validated in the same form boundary as category fields. |
| `axios` | 1.13.x existing | Centralized API client for transitional endpoints | Keep using `frontend/src/lib/api.ts` and update the category/budget methods there first so page components stay dumb. |
| `i18next` + `react-i18next` | 25.8.x + 16.5.x existing | Rebrand strings and new budget copy | Use for all Flux renames and budget terminology changes. Do not hardcode renamed labels in components. |
| `decimal` via Pydantic/SQLAlchemy numeric handling | existing backend behavior | Accurate budget amount handling | Keep `Numeric(15,2)` and backend `Decimal` handling. Finance data should not move to float-based persistence. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| Alembic | Brownfield schema + data migrations | Use multiple small migrations: additive columns first, backfill second, cleanup last. Do not combine schema removal with backfill in one irreversible step. |
| pytest + pytest-asyncio | Backend regression coverage for migration logic | Add targeted tests around effective-budget backfill, category CRUD with budget fields, and dashboard/report compatibility. |
| ESLint + TypeScript build | Frontend contract safety during API shape changes | Run `npm run build` after type changes to catch all `group_id` and `Budget` type fallout quickly. |

## Prescriptive Implementation Approach

### 1. Keep the runtime stack; change the domain model in place

Do not introduce new frontend or backend frameworks for this v2. The current stack already supports the work:

- frontend forms and route updates in React
- API contract changes in FastAPI
- relational migration and backfill in Alembic/PostgreSQL

This is a narrow domain simplification. The risk is data compatibility, not framework capability.

### 2. Move the canonical editable budget state onto `categories`

Recommended category columns:

- `has_budget BOOLEAN NOT NULL DEFAULT false`
- `budget_amount NUMERIC(15,2) NULL`
- `budget_currency VARCHAR(3) NULL`

Recommended interpretation:

- `has_budget=false` means no category budget
- `has_budget=true` requires `budget_amount`
- `budget_currency` can default to the user primary currency at write/read time if the product does not need per-category FX complexity in v2

Recommendation: do **not** recreate the old standalone budget UX inside a new abstraction layer. Category create/edit should own the budget fields directly.

Confidence: MEDIUM-HIGH. This is the cleanest fit for the stated v2 scope, but it intentionally simplifies the old month-versioned budget model.

### 3. Use a staged brownfield migration, not a flag day rewrite

Recommended sequence:

1. Add category budget columns and expose them in category schemas.
2. Backfill each category from the current effective budget.
3. Switch frontend category flows and dashboard/report reads to the new category-owned fields.
4. Keep legacy `budgets` reads temporarily only where needed for compatibility or export.
5. Remove standalone budgets UI and `category_groups` references.
6. Drop legacy tables/endpoints only after app code no longer depends on them.

Backfill rule for existing data:

- For each category, prefer the current-month override if one exists.
- Otherwise use the most recent recurring/default budget effective at migration time.
- If neither exists, set `has_budget=false`.

This mirrors current service semantics closely enough to avoid surprising users on cutover day.

Confidence: HIGH.

### 4. Treat `budgets` as legacy storage during transition, not as the v2 source of truth

Recommendation:

- Stop writing new budget state to `budgets` once category-owned budgets ship.
- Keep `budgets` readable during the transition only if dashboard/report/export paths still need it.
- If historical month-by-month budget history matters later, model that as a separate future feature, not as hidden complexity in this narrow v2.

Why: the current `budgets` table represents time-versioned and recurring rules. The requested v2 model is simpler and category-owned. Trying to preserve both as first-class runtime models will create permanent ambiguity.

Confidence: HIGH.

### 5. Remove category groups with additive-first schema changes

Recommended database sequence:

1. Remove frontend usage of `group_id` and `/category-groups`.
2. Stop backend writes and reads that require category groups.
3. Add migration to null or ignore `group_id` references during the app cutover.
4. Drop FK/column/table only after API and UI no longer touch them.

Do not drop `category_groups` first. The existing code references it in models, services, exports, and dashboard comparison responses.

Confidence: HIGH.

## Installation

```bash
# Runtime stack: keep existing majors
# No new core runtime packages are required for this v2

# Backend verification
cd backend && pytest

# Frontend verification
cd frontend && npm run build && npm run lint
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Category-owned budget columns on `categories` | Keep `budgets` as the primary model and only hide the old UI | Use this only if month-specific budget history must remain editable in v2. That does not match the stated scope well. |
| Existing React SPA | Next.js / React Router framework-mode adoption | Use only if there is a separate initiative for SSR, loaders/actions migration, or SEO. None of that helps this finance-domain change. |
| SQLAlchemy + Alembic migration | Introduce Prisma/SQLModel/another ORM layer | Use only in a future rewrite. Adding a second persistence abstraction in a finance brownfield migration is unnecessary risk. |
| TanStack Query cache invalidation | Redux/Zustand/RTK for migration state | Use only if there is a broader client-state problem. This v2 is CRUD-heavy and already fits Query well. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Next.js, Remix, or router framework migration | Large architectural churn for no gain on a narrow self-hosted SPA migration | Keep Vite + React Router 7 and update routes in place |
| New state-management layer (`redux`, `zustand`, `mobx`) | Adds another source of truth while TanStack Query already handles the server-state workload | Keep Query + local component state |
| New ORM or schema framework | Mid-migration persistence rewrites are high risk in finance data | Keep SQLAlchemy 2 async + Alembic |
| Dual-write long term between `categories` and `budgets` | Creates drift and unclear ownership | Short transitional read compatibility, then single-write to `categories` |
| Broad design-system refresh | Rebrand and data-model migration already create enough UI churn | Keep current shadcn/Tailwind language and update copy/icons only |
| Event-driven migration architecture | Over-engineered for a single-app relational data cutover | Use synchronous service-layer migration and DB backfill |

## Stack Patterns by Variant

**If v2 keeps only one active monthly/default budget per category:**
- Store it directly on `categories`
- Because that matches the requested category-owned model and simplifies all CRUD flows

**If product requirements later reintroduce month-versioned budgets:**
- Keep `categories` as the ownership boundary and add a separate `category_budget_snapshots` or overrides model later
- Because ownership and history are different concerns and should not be conflated again

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| `react@19.2.x` | `react-router-dom@7.13.x` | Existing repo pairing; keep current major versions for this migration. |
| `react@19.2.x` | `@tanstack/react-query@5.90.x` | Existing repo pairing; sufficient for mutation-driven screens. |
| `fastapi>=0.109` | `pydantic>=2.5` | Existing backend line already uses Pydantic v2 style settings/models. |
| `sqlalchemy>=2.0` | `alembic>=1.13` | Correct pair for additive schema and data migration workflow. |
| `tailwindcss@4.2.x` | modern browsers only | Fine for the existing responsive web target; no CSS stack change needed. |

## Migration and Compatibility Concerns

- API compatibility: add category budget fields to `CategoryRead`, `CategoryCreate`, and `CategoryUpdate` before removing budget endpoints.
- Frontend compatibility: update `frontend/src/types/index.ts` and `frontend/src/lib/api.ts` first, then convert pages. This will surface all compile-time fallout from `group_id` and legacy `Budget` usage.
- Dashboard/report compatibility: refactor budget comparison reads to use category-owned budget fields, or provide a temporary server adapter that emits the existing comparison shape from category budget data.
- Export/import compatibility: version export payloads or keep legacy `budgets`/`category_groups` export keys during one transition release so existing backups remain restorable.
- Data loss risk: dropping `budgets` too early will destroy month-versioned history. Keep it until backfill is verified and retention policy is explicit.
- Rebrand scope: rename user-facing copy and product identifiers to Flux, but avoid unnecessary package/module renames unless they block clarity or deployment.

## Sources

- Local codebase: [`.planning/PROJECT.md`](/workspaces/flux-pluggy/securo/.planning/PROJECT.md), [`.planning/codebase/ARCHITECTURE.md`](/workspaces/flux-pluggy/securo/.planning/codebase/ARCHITECTURE.md), [`.planning/codebase/STACK.md`](/workspaces/flux-pluggy/securo/.planning/codebase/STACK.md)
- Local codebase: [`frontend/package.json`](/workspaces/flux-pluggy/securo/frontend/package.json), [`backend/pyproject.toml`](/workspaces/flux-pluggy/securo/backend/pyproject.toml), [`backend/app/services/budget_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/budget_service.py), [`backend/app/services/category_service.py`](/workspaces/flux-pluggy/securo/backend/app/services/category_service.py)
- React Router official docs: https://reactrouter.com/
- TanStack Query React docs: https://tanstack.com/query/latest/docs/framework/react/overview
- FastAPI official docs: https://fastapi.tiangolo.com/
- SQLAlchemy asyncio docs: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Alembic operations docs: https://alembic.sqlalchemy.org/en/latest/ops.html
- Tailwind CSS compatibility docs: https://tailwindcss.com/docs/compatibility

---
*Stack research for: brownfield self-hosted personal finance web app v2*
*Researched: 2026-04-03*
