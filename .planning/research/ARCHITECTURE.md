# Architecture Research

**Domain:** Brownfield self-hosted personal finance SPA rework for category-owned budgeting and product rebrand
**Researched:** 2026-04-03
**Confidence:** HIGH

## Standard Architecture

### System Overview

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                              React SPA Layer                                │
├──────────────────────────────────────────────────────────────────────────────┤
│  ┌────────────────┐  ┌──────────────────┐  ┌─────────────────────────────┐  │
│  │ App Shell      │  │ Category Screens │  │ Dashboard / Reports / Rules │  │
│  │ brand + nav    │  │ create/edit/list │  │ read effective budget state │  │
│  └──────┬─────────┘  └────────┬─────────┘  └──────────────┬──────────────┘  │
│         │                     │                           │                 │
├─────────┴─────────────────────┴───────────────────────────┴─────────────────┤
│                              API Contract Layer                             │
├──────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────┐   ┌──────────────────────────────────────────┐  │
│  │ Category Aggregate API  │   │ Read Models for budget comparison/report │  │
│  │ categories own budget   │   │ derived from category + effective budget │  │
│  └────────────┬────────────┘   └──────────────────────┬───────────────────┘  │
│               │                                       │                      │
├───────────────┴───────────────────────────────────────┴──────────────────────┤
│                           FastAPI Domain Services                            │
├──────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────┐  ┌────────────────────────┐  ┌──────────────────┐  │
│  │ Category Service     │  │ Budget Policy Service  │  │ Branding Config  │  │
│  │ aggregate owner      │  │ effective month logic  │  │ product metadata │  │
│  └──────────┬───────────┘  └─────────────┬──────────┘  └─────────┬────────┘  │
│             │                            │                       │           │
├─────────────┴────────────────────────────┴───────────────────────┴───────────┤
│                           PostgreSQL Persistence                             │
│  ┌─────────────────────┐  ┌──────────────────────────┐  ┌─────────────────┐ │
│  │ categories          │  │ budget value records     │  │ users / config  │ │
│  │ group removed       │  │ current + historical     │  │ export metadata  │ │
│  └─────────────────────┘  └──────────────────────────┘  └─────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| Category aggregate | Own category identity plus budget configuration exposed to UI | Extend `Category` schemas and `category_service` so create/update/read includes budget fields |
| Budget policy service | Resolve effective budget for a month, preserve recurring and history semantics | Keep budget-resolution logic as a dedicated service around the current `budgets` table or its renamed successor |
| Reporting read models | Serve dashboard/report/budget comparison consumers from the new aggregate model | Query layer joining categories with effective budget values, not group metadata |
| Branding config | Centralize product name, copy, title, export filename, and logo references | Small backend settings surface plus frontend brand constants/context |

## Recommended Project Structure

```text
backend/app/
├── api/
│   ├── categories.py            # category aggregate write/read endpoints
│   ├── budget_views.py          # transitional read-only budget comparison/list endpoints
│   └── export.py                # brand-aware export metadata and filenames
├── services/
│   ├── category_service.py      # aggregate owner for category + budget config
│   ├── category_budget_service.py # effective budget resolution and backfill helpers
│   └── branding_service.py      # product name / display metadata
├── schemas/
│   ├── category.py              # includes budget settings in read/write contracts
│   └── budget_view.py           # read models for budget tables and comparisons
└── models/
    ├── category.py              # no group foreign key; has budget flag/config
    └── budget.py                # internal time-based budget values until retired

frontend/src/
├── lib/
│   ├── api.ts                   # category aggregate endpoints
│   └── brand.ts                 # app name, logo, document title helpers
├── pages/
│   ├── categories.tsx           # primary budget create/edit UX
│   ├── budgets.tsx              # transitional overview or redirect
│   └── dashboard.tsx            # consumes budget read models without groups
├── components/
│   ├── category-form.tsx        # shared create/edit form with budget fields
│   └── app-layout.tsx           # brand-aware shell
└── types/
    └── index.ts                 # category aggregate replaces separate group dependence
```

### Structure Rationale

- **`category_service` as the aggregate owner:** the new requirement is category-owned budgeting, so writes must enter through categories, not a parallel budgets CRUD surface.
- **Separate budget policy/read-model service:** monthly and recurring logic in `backend/app/services/budget_service.py` is still real complexity. Keep it, but demote it from user-facing aggregate owner to internal resolver.
- **Dedicated branding seam:** the rebrand touches app shell, export filenames, and copy. A small brand config module avoids another repo-wide string hunt later.

## Architectural Patterns

### Pattern 1: Aggregate-Owned Writes

**What:** Category create/update endpoints accept budget fields and orchestrate any supporting budget-record writes in one transaction.
**When to use:** Any create/edit/delete flow where the user thinks in terms of a category, not a detached budget entity.
**Trade-offs:** Simpler UX and cleaner ownership; backend write paths become slightly fatter because one request may mutate multiple tables.

**Example:**
```typescript
type CategoryBudgetInput = {
  has_budget: boolean
  budget_mode?: 'monthly'
  default_amount?: number | null
  effective_month?: string | null
}

type CategoryUpsert = {
  name: string
  icon: string
  color: string
  budget?: CategoryBudgetInput
}
```

### Pattern 2: Internal Temporal Budget Records

**What:** Keep time-variant budget values in a separate persistence structure even after budget ownership moves to categories.
**When to use:** When monthly overrides and recurring effective-from behavior must survive without flattening history into the category row.
**Trade-offs:** Two persistence concepts remain, but the user-visible model is much cleaner and you avoid breaking reporting logic.

**Example:**
```python
async def update_category_with_budget(session, category_id, user_id, data):
    category = await update_category_fields(session, category_id, user_id, data)
    if data.budget:
        await upsert_effective_budget_value(
            session,
            category_id=category.id,
            user_id=user_id,
            has_budget=data.budget.has_budget,
            amount=data.budget.default_amount,
            effective_month=data.budget.effective_month,
        )
    return await get_category_with_budget(session, category.id, user_id)
```

### Pattern 3: Expand-Migrate-Cutover

**What:** Add new schema/API shape first, backfill and dual-read second, remove legacy groups and write paths last.
**When to use:** Brownfield domain changes where dashboard, rules, export, and UI all depend on the old shape.
**Trade-offs:** Temporary compatibility code is unavoidable, but it is much safer than a one-shot replacement.

## Data Flow

### Request Flow

```text
[User edits category]
    ↓
[Category form]
    ↓
[categories API upsert]
    ↓
[category_service]
    ↓
[category row update] + [effective budget value upsert]
    ↓
[category aggregate response]
    ↓
[query invalidation for categories, dashboards, reports]
```

### State Management

```text
[TanStack Query cache]
    ↓
[categories query] ←→ [category mutations]
    ↓
[dashboard/reports queries re-fetch derived read models]
```

### Key Data Flows

1. **Category create/edit:** Frontend submits category identity plus optional budget config; backend writes category fields and the effective budget record in one transaction.
2. **Budget comparison/dashboard:** Consumers ask for derived read models; backend resolves effective month budget from category-owned config plus temporal records, without exposing group joins.
3. **Rebrand propagation:** Frontend app shell and backend export metadata both read from centralized brand configuration so copy changes are atomic instead of scattered.

## Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| `frontend/src/components/category-form.tsx` | Render category identity and budget controls in one form | `frontend/src/lib/api.ts`, query invalidation |
| `frontend/src/pages/budgets.tsx` | Transitional list/report surface only; no primary create/edit ownership | budget read-model endpoints, category edit route |
| `backend/app/api/categories.py` | Public write boundary for category and budget config | `category_service`, schemas |
| `backend/app/services/category_service.py` | Aggregate coordinator for category lifecycle | SQLAlchemy models, `category_budget_service` |
| `backend/app/services/category_budget_service.py` | Effective budget resolution, backfill, compatibility reads | `Budget` model or successor table, dashboard/report services |
| `backend/app/api/category_groups.py` | Legacy boundary to retire after migration | none after cutover |
| `backend/app/api/export.py` | Export format/version and branded filenames | category aggregate, internal budget values, branding config |

## Data / Model Migration Implications

### Recommended Target Model

- `categories` becomes the public domain aggregate.
- Remove `categories.group_id` and stop joining `category_groups` in dashboard budget reads.
- Add category-owned budget fields for intent, at minimum:
  - `has_budget` boolean
  - optional budget metadata such as `budget_currency` if needed
  - optionally `budget_anchor_month` if you want the category row to expose when the current recurring value begins
- Keep temporal budget values in a separate internal table until monthly override behavior is deliberately simplified. The current `budgets` table already encodes effective-from recurring values and one-off month overrides; that logic should not be forced into a single category row.

### Migration Sequence

1. **Expand schema**
   - Add new category columns for budget ownership.
   - Make `group_id` nullable everywhere already, then stop requiring it in API contracts.
   - Keep `budgets` and `category_groups` tables untouched for the moment.
2. **Backfill category ownership**
   - Set `has_budget = true` for categories that have any rows in `budgets`.
   - Derive the category-level current/default value from the latest recurring budget row where available.
   - Leave historical/month-specific rows in `budgets`.
3. **Dual-read / dual-write period**
   - Category reads return embedded budget state.
   - Category writes update category columns and also maintain budget rows.
   - Existing `/api/budgets/comparison` can remain, but it should read through the new resolver path.
4. **Consumer cutover**
   - Frontend category forms become the only place to create/edit budget settings.
   - Budgets page becomes overview-only or redirects into category editing.
   - Dashboard, reports, export, and types stop expecting group metadata.
5. **Legacy removal**
   - Remove `/api/category-groups`, group services, group schemas, and `group_id` from types/contracts.
   - Remove standalone budget-create/update routes if no longer needed.
   - Bump export format version and rename filenames from `securo-*` to `flux-*`.

### Migration Risks

- The current budget logic is temporal, not just categorical. Dropping the `budgets` table too early would break recurring effective-from behavior.
- `create_default_categories()` currently depends on `create_default_groups()`. That bootstrap path must be rewritten before group deletion.
- `BudgetVsActual` currently emits `group_id` and `group_name`. Removing groups is a contract change for dashboard consumers and frontend types.
- Backup/export currently serializes both `category_groups` and `budgets`, and still emits a `securo-backup-*` filename. Rebrand and data-model migration intersect here.

## Suggested Build Order

1. **Introduce branding seam first**
   - Add a single frontend/backend source for product name and asset references.
   - Convert obvious shell/export strings to read from it.
   - Reason: low-risk, reduces churn while deeper domain work is landing.

2. **Expand backend schema for category-owned budget metadata**
   - Add category budget columns and migration scripts.
   - Rewrite default-category creation so it no longer depends on groups.
   - Reason: this creates the safe landing zone for all later API changes.

3. **Move public write ownership to categories**
   - Extend category schemas and endpoints with embedded budget payloads.
   - Keep temporal budget resolution behind an internal service.
   - Reason: category create/edit is the user-facing requirement; this is the real architectural cutover.

4. **Cut over frontend forms and types**
   - Merge budget controls into category create/edit UX.
   - Change SPA types and queries to consume category aggregates.
   - Demote the budgets page to overview/redirect.

5. **Migrate downstream readers**
   - Update dashboard, reports, export, and any rule/category displays to stop depending on group data.
   - Reason: these consumers are numerous; they should switch only after the new aggregate contract is stable.

6. **Delete legacy group and standalone budget surfaces**
   - Drop `category_groups` API/service/model usage.
   - Remove old budget CRUD endpoints if they no longer have a product role.
   - Finalize rebrand leftovers and export format versioning.

### Why This Order

- Branding can land independently and early.
- Schema expansion before API cutover prevents risky flag days.
- Category aggregate writes must exist before the SPA can move budget editing into category flows.
- Reader migration must follow contract stabilization, not precede it.
- Physical deletion of groups and legacy budget paths should be the last step because they are still referenced by bootstrap, dashboard comparisons, and export.

## Anti-Patterns

### Anti-Pattern 1: Replace the budget table with category columns in one shot

**What people do:** Move `amount` onto `categories` and delete the temporal model immediately.
**Why it's wrong:** The current app supports recurring defaults plus month-specific overrides. A single row on `categories` cannot represent that history cleanly.
**Do this instead:** Make categories the public owner, but keep an internal temporal budget record model until behavior is intentionally simplified.

### Anti-Pattern 2: Remove category groups before consumer cutover

**What people do:** Drop `category_groups` and `group_id` first because the new UX no longer needs them.
**Why it's wrong:** Default category bootstrap, budget comparison schemas, frontend types, and export still reference groups.
**Do this instead:** Expand, dual-read, migrate consumers, then remove the table and routes last.

### Anti-Pattern 3: Scatter the rebrand through literals

**What people do:** Change visible strings page by page without adding a brand config seam.
**Why it's wrong:** You miss filenames, metadata, document titles, and future onboarding/export surfaces.
**Do this instead:** Centralize brand identity in a small shared config surface and consume it everywhere.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| PostgreSQL via SQLAlchemy | transactional aggregate writes + reporting reads | Brownfield monolith is still the right shape; no service split is needed |
| Celery/Redis | unchanged for this work | category-budget migration is request-path heavy, not background-job heavy |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| categories API ↔ category service | direct service call | becomes the canonical write path |
| category service ↔ budget policy service | direct service call | isolates temporal budget logic from CRUD/API concerns |
| dashboard/report services ↔ budget policy service | direct service call | all budget reads should resolve through one effective-budget path |
| frontend app shell ↔ brand config | direct import | prevents string drift during rebrand |

## Sources

- `.planning/PROJECT.md`
- `.planning/codebase/ARCHITECTURE.md`
- `.planning/codebase/STACK.md`
- `backend/app/models/category.py`
- `backend/app/models/budget.py`
- `backend/app/services/category_service.py`
- `backend/app/services/category_group_service.py`
- `backend/app/services/budget_service.py`
- `backend/app/api/category_groups.py`
- `backend/app/api/export.py`
- `frontend/src/lib/api.ts`
- `frontend/src/pages/budgets.tsx`
- `frontend/src/types/index.ts`

---
*Architecture research for: self-hosted personal finance SPA rework*
*Researched: 2026-04-03*
