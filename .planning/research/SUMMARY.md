# Project Research Summary

**Project:** Flux
**Domain:** Brownfield self-hosted personal finance SPA v2 rebrand and category-budget model migration
**Researched:** 2026-04-03
**Confidence:** HIGH

## Executive Summary

Flux v2 is a constrained brownfield migration on an existing React + FastAPI + PostgreSQL personal-finance app, not a greenfield product build. The research is consistent on the core point: experts would keep the current runtime stack, preserve the current responsive SPA UX, and change the domain model in place. The product-level goal is to simplify category and budgeting workflows by removing category groups, making categories the public ownership boundary for budget settings, and rebranding Securo to Flux without broad visual churn.

The recommended approach is staged and additive. Introduce a centralized branding seam first, then expand the backend schema and API so categories expose budget state directly, then move category create/edit into the single source of truth for budget configuration, then cut downstream readers over, and only after parity is verified remove legacy group and standalone budget surfaces. This is the lowest-risk path because the real complexity is not UI rendering or framework capability; it is preserving historical budget meaning, report parity, exports, and migration trust.

The key risk is oversimplifying a time-aware budget system into a naive category scalar too early. The research repeatedly warns that budget semantics, dashboard/report consumers, backup/export formats, and default category bootstrap paths still depend on legacy structures. Mitigation is clear: use expand-migrate-contract, keep temporal budget resolution behind a dedicated backend service during transition, version export compatibility deliberately, and prove parity with fixture-based regression tests before deleting legacy paths.

## Key Findings

### Recommended Stack

The stack recommendation is conservative by design: keep the existing React 19 SPA, React Router 7, TanStack Query 5, FastAPI, SQLAlchemy 2 async, Alembic, and PostgreSQL 16. This work is a domain migration with compatibility constraints, so replacing frameworks or introducing new state or ORM layers would add risk without solving the actual problem.

Implementation should lean on the tools already in the repo: `react-hook-form` plus `zod` for a unified category form with optional budget fields, the existing centralized `axios` API client for contract cutover, i18n for all Flux rebrand copy, and additive Alembic migrations with targeted backend/frontend regression checks. Critical version guidance is mostly “stay on existing majors”; the important requirement is compatibility discipline, not upgrades.

**Core technologies:**
- React 19.2.x: SPA UI for category, budget, and rebrand flows — keep the existing rendering model and avoid churn.
- React Router 7.13.x: route-level migration for categories, budgets, and redirects — supports incremental cutover without framework-mode adoption.
- TanStack Query 5.90.x: server-state reads and mutations — enough for transitional invalidation and downstream reader cutover.
- FastAPI 0.109+: API contracts for category-owned budget writes and compatibility reads — extends existing router/service/schema patterns cleanly.
- SQLAlchemy 2 async + Alembic 1.13+: schema evolution and data backfill — the correct brownfield tooling for staged migration.
- PostgreSQL 16: persistent source of truth — keep one relational source of truth and migrate ownership there.

### Expected Features

Launch scope is sharply defined. Users must be able to create and edit categories with an explicit budget-enabled state, budget amount, and clear “not budgeted” behavior, all inside a compact mobile-usable flow. Removing groups is acceptable only if the flat category list remains searchable, ordered, and manageable, and only if historical transactions, rules, recurring items, reports, and budget surfaces continue to resolve categories correctly after migration.

Differentiation comes from making the simplification feel intentional rather than reduced: one flow for category metadata and budget setup, migration messaging that explains what changed and what did not, and fast mobile edits from the places users already feel friction. The research is equally clear on what not to do: do not reintroduce groups under another name, do not force every category to have a budget, and do not leave category and budget CRUD as competing authorities.

**Must have (table stakes):**
- Category create/edit with inline budget toggle and amount — users expect category and budget setup in one place.
- Flat category list with search, ordering, and mobile usability — group removal needs replacement organization affordances.
- Consistent budget/report reads from category-owned settings — transaction edits and budget totals must stay in sync.
- Migration-safe preservation of historical category usage — transactions, rules, recurring items, and reports must survive unchanged.
- Compact responsive category/budget editing — common fixes must work cleanly on mobile.

**Should have (competitive):**
- Single-flow category setup for metadata and budgeting — strongest UX payoff of the v2 simplification.
- Migration preview or post-migration explanation — protects user trust during a brownfield change.
- Smart defaults from history when enabling a budget — reduces setup friction.
- Mobile quick actions from category and transaction views — lets users fix budgeting issues at the point of pain.

**Defer (v2+):**
- Historical-spend suggestions for budget amounts.
- Inline “save rule for future transactions” prompts after recategorization.
- Archive/hide refinements and larger-list segmentation improvements.
- Tags, advanced reallocation workflows, and richer budget strategies such as rollover.

### Architecture Approach

The architecture recommendation is to make the category aggregate the public ownership boundary while preserving temporal budget behavior behind a dedicated policy/resolution layer until the team intentionally decides to simplify history semantics. Branding also needs a small centralized config seam so the Flux rename lands consistently across UI, exports, metadata, and operational surfaces. Frontend pages and backend read models should be cut over incrementally against stabilized contracts, with group removal and standalone budget deletion happening last.

**Major components:**
1. Category aggregate API/service — own category identity plus budget configuration in read/write contracts.
2. Budget policy service — preserve effective-month and recurring budget semantics during transition and backfill.
3. Reporting read models — serve dashboard, report, and budget comparison consumers from the new aggregate contract.
4. Branding config — centralize product name, titles, backup filenames, logos, and related metadata.

### Critical Pitfalls

1. **Treating group removal as a simple column drop** — avoid with an expand-migrate-contract rollout that updates API schemas, frontend types, exports, defaults, and reporting before destructive cleanup.
2. **Corrupting historical budget meaning** — avoid by preserving effective-month semantics behind a dedicated service, defining time scope explicitly in UX, and validating old vs new report parity with fixtures.
3. **Keeping category and budget workflows half-merged** — avoid by making categories the single write authority and demoting or removing the standalone budgets CRUD surface on a deliberate schedule.
4. **Shipping a cosmetic rebrand only** — avoid by inventorying and updating operational identity surfaces such as backup filenames, compose/config defaults, browser titles, docs, and tests.
5. **Breaking imports/exports without versioning** — avoid by versioning the export contract or shipping a converter and validating restore from v1 data into v2.

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 1: Branding Seam and Contract Inventory
**Rationale:** This is low-risk, reduces rename churn later, and forces explicit decisions on compatibility before domain work starts.
**Delivers:** Centralized Flux branding config, inventory of brand surfaces, compatibility policy for export naming and operational identifiers, and a full dependency map for `group_id`, `category_groups`, and budget readers.
**Addresses:** Rebrand requirement, migration safety, trust-building communication groundwork.
**Avoids:** Cosmetic rebrand drift, hidden group dependencies, and unplanned contract breaks.

### Phase 2: Schema Expansion and Budget Ownership Model
**Rationale:** The backend needs a safe landing zone before the SPA can move budget editing into category flows.
**Delivers:** Additive schema changes for category-owned budget metadata, rewritten default-category bootstrap without group dependence, backfill strategy, and a dedicated budget policy service or equivalent compatibility layer.
**Uses:** FastAPI, SQLAlchemy, Alembic, PostgreSQL, pytest.
**Implements:** Category aggregate ownership plus temporal budget-resolution boundary.

### Phase 3: Category-Centric UX and API Cutover
**Rationale:** Once contracts exist, the user-facing source of truth must move to category create/edit flows.
**Delivers:** Unified category form with budget controls, updated API client/types, mobile-usable category editing, and a budgets page that is reduced to overview, redirect, or read-only planning.
**Addresses:** Inline budget editing, explicit no-budget state, compact mobile UX, single-flow setup.
**Avoids:** Duplicate budget ownership, inconsistent validation, and stale query invalidation behavior.

### Phase 4: Downstream Reader Migration and Parity Validation
**Rationale:** Dashboard, reports, exports, and transaction-adjacent workflows should move only after the new aggregate contract is stable.
**Delivers:** Budget-vs-actual, reports, exports, and related readers resolved from the new category-owned contract via one effective-budget path, plus fixture and restore parity verification.
**Addresses:** Consistent budget/report reads, migration-safe historical behavior, export compatibility.
**Avoids:** Historical budget corruption, report drift, and broken backup/restore flows.

### Phase 5: Legacy Removal and Final Rebrand Cleanup
**Rationale:** Destructive cleanup belongs last, after parity is proven and overlapping paths are no longer needed.
**Delivers:** Removal of `category_groups` APIs/services/models, deletion of old budget CRUD routes if obsolete, final type/schema cleanup, repo-wide Flux naming completion, and export format/version finalization.
**Addresses:** Removal of groups from domain and UI, cleanup of old budget surfaces, full rebrand completion.
**Avoids:** Partial migrations, long-lived dual ownership, and support confusion from mixed product identity.

### Phase Ordering Rationale

- Branding and compatibility inventory come first because they are low-risk and reduce later churn across code, docs, tests, and export surfaces.
- Schema expansion precedes UX work because category forms cannot safely become the write authority until backend ownership and migration paths exist.
- Reader migration follows contract stabilization because dashboard/report/export consumers are numerous and sensitive to semantic drift.
- Destructive removal comes last because legacy groups and budgets are still referenced by bootstrap logic, comparisons, exports, and historical semantics.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2:** Budget ownership model and migration semantics need explicit decisions on whether v2 preserves month-specific and recurring behavior exactly or introduces a deliberate simplification boundary.
- **Phase 4:** Export/restore compatibility and report parity validation need focused planning because they are high-trust, high-regression areas.

Phases with standard patterns (skip research-phase):
- **Phase 1:** Branding seam and inventory work is straightforward brownfield refactoring.
- **Phase 3:** Unified React form and API client/type cutover follows established repo patterns and well-documented tooling.
- **Phase 5:** Legacy removal is standard once parity and dependency elimination are proven.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Strong alignment between project scope, local codebase reality, and official framework/database docs; recommendation is to keep the existing stack. |
| Features | MEDIUM | Feature guidance is coherent, but some differentiators are based on competitor patterns and product inference rather than validated user demand. |
| Architecture | HIGH | Directly grounded in the current repo structure and consistent across all research outputs. |
| Pitfalls | HIGH | The highest-risk issues are well-supported by codebase dependencies and standard migration failure modes in brownfield finance systems. |

**Overall confidence:** HIGH

### Gaps to Address

- **Historical budget semantics:** The project brief wants category-owned budgets, but the current system has recurring and month-specific behavior. Planning must decide exactly what “edit budget” means over time.
- **Export/import compatibility policy:** The roadmap must choose whether v2 restores v1 archives natively, via converter, or via explicit version gating.
- **Flat-list organization details:** Research strongly recommends search, ordering, and hide/archive affordances after group removal, but the exact MVP slice should be validated against current UX debt and user dataset size.
- **Operational rename boundary:** Some internal identifiers may need to remain stable for compatibility. Planning should define which `Securo` names become `Flux` now and which remain intentionally unchanged.

## Sources

### Primary (HIGH confidence)
- [`.planning/research/STACK.md`](/workspaces/flux-pluggy/securo/.planning/research/STACK.md) — stack recommendations, migration strategy, compatibility constraints
- [`.planning/research/ARCHITECTURE.md`](/workspaces/flux-pluggy/securo/.planning/research/ARCHITECTURE.md) — aggregate boundaries, component responsibilities, migration order
- [`.planning/research/PITFALLS.md`](/workspaces/flux-pluggy/securo/.planning/research/PITFALLS.md) — risk analysis, warning signs, prevention phases
- [`.planning/PROJECT.md`](/workspaces/flux-pluggy/securo/.planning/PROJECT.md) — project scope, constraints, active requirements
- https://reactrouter.com/ — route-level incremental migration guidance
- https://tanstack.com/query/latest/docs/framework/react/overview — server-state mutation and invalidation patterns
- https://fastapi.tiangolo.com/ — API/schema extension patterns
- https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html — async ORM patterns already aligned with repo usage
- https://alembic.sqlalchemy.org/en/latest/ops.html — staged migration tooling

### Secondary (MEDIUM confidence)
- [`.planning/research/FEATURES.md`](/workspaces/flux-pluggy/securo/.planning/research/FEATURES.md) — feature expectations, prioritization, anti-features
- https://actualbudget.org/docs/budgeting/categories/ — category and budgeting organization patterns
- https://help.monarch.com/hc/en-us/articles/360048883631-Creating-Your-Budget-in-Monarch — competitive expectations around budget management
- https://docs.prisma.io/docs/guides/database/data-migration — expand/contract migration pattern used by analogy

### Tertiary (LOW confidence)
- https://www.reddit.com/r/MonarchMoney/comments/1igrik4 — user-trust signal around budget-history failures; useful as caution, not as a design authority

---
*Research completed: 2026-04-03*
*Ready for roadmap: yes*
