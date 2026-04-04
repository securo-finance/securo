# Phase 9: Period-Linked Records & Cleanup - Context

**Gathered:** 2026-04-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Introduce a durable monthly-period record model that active financial data can reference, and remove the remaining supported runtime/backend exposure of category groups so the monthly workflow no longer depends on that legacy structure.

</domain>

<decisions>
## Implementation Decisions

### Monthly Period Persistence
- Replace implicit date-only lookup as the primary active-month source with a durable monthly-period record that can be linked from transactions and account setup.
- Keep the user preference added in Phase 8 as the selector for the active month, but back it with persisted monthly-period records so later snapshot phases have a stable anchor.
- Treat the active monthly period as reusable shared state across dashboard, transactions, accounts, and sync ingestion rather than recomputing from transaction dates alone.

### Record Linkage Scope
- Transactions created manually, via sync, via import, or via opening-balance bootstrap should all attach to the active monthly period.
- New account creation should capture the intended current-month period immediately so later reporting and snapshot closure can resolve account-origin data against that month.
- This phase should focus on storing and querying by period identity; month-closing and snapshot immutability remain deferred to later phases.

### Category Group Cleanup Boundary
- Remove category-group concepts from supported APIs and active monthly-finance workflows.
- Prefer deleting or severing active ORM/database dependencies that keep categories tied to `group_id` in the supported model.
- Preserve only the minimum compatibility surface that is still intentionally required; otherwise treat category-group code as legacy to remove from active runtime paths.

### the agent's Discretion
- Exact naming of the monthly-period model and foreign keys.
- Whether transaction/account read responses expose period identifiers directly in this phase or only use them internally for query correctness.
- How much legacy export compatibility remains acceptable once supported APIs and runtime flows are clean.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/app/services/month_service.py` already normalizes and resolves the active month preference from Phase 8.
- `backend/app/services/transaction_service.py`, `backend/app/services/connection_service.py`, `backend/app/services/import_service.py`, and `backend/app/services/account_service.py` are the main transaction/account write paths that need period linkage.
- `backend/app/services/dashboard_service.py` already centralizes most month-filtered reads and is the main integration point for period-aware queries.

### Established Patterns
- Active data mutations are service-owned, with routers mapping `ValueError` to `400`.
- The frontend consumes backend state through `frontend/src/lib/api.ts` plus TanStack Query hooks, so any new period-aware read contract should plug into that shape.
- Schema changes still require Alembic migrations, while tests rely on SQLAlchemy metadata create-all for new tables/columns.

### Integration Points
- Account creation currently creates an opening-balance transaction in `account_service.py`, so period linkage must cover both the account record and the bootstrap transaction.
- Sync ingestion in `connection_service.py` is the main source of bulk transaction creation and cannot keep inferring month membership only from transaction dates.
- `backend/app/main.py`, `frontend/src/types/index.ts`, and any export or legacy API entry points still revealing category-group concepts need cleanup to satisfy the supported-surface requirement.

</code_context>

<specifics>
## Specific Ideas

- Introduce a `monthly_periods` table keyed by user and `YYYY-MM`, with helpers that resolve or create the current period on demand.
- Update dashboard and transaction reads to filter by linked period where possible instead of only date windows.
- Remove `/api/category-groups` from the supported app surface and trim shared frontend types that still advertise category groups.

</specifics>

<deferred>
## Deferred Ideas

- Month-closing commands, snapshot materialization, and closed-history confirmation UX.
- Full archive/export redesign beyond what is needed to stop supported runtime dependence on category groups.

</deferred>
