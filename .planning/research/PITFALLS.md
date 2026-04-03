# Pitfalls Research

**Domain:** Self-hosted personal finance web app v2 rebrand and category-budget model migration
**Researched:** 2026-04-03
**Confidence:** HIGH

## Critical Pitfalls

### Pitfall 1: Treating group removal as a simple column drop

**What goes wrong:**
The team removes `category_groups` and `categories.group_id` quickly, but grouped behavior is still embedded in defaults, API payloads, export backups, budget reporting, and frontend selectors. The result is a partial migration where some screens work, but exports, dashboard comparisons, or seed/default data still assume groups exist.

**Why it happens:**
The visible UI change looks small, but current code couples groups into the category model, default category creation, budget comparison payloads, and backup export. Relevant paths already include `backend/app/models/category.py`, `backend/app/services/category_service.py`, `backend/app/services/category_group_service.py`, `backend/app/services/budget_service.py`, `backend/app/api/export.py`, and `frontend/src/pages/budgets.tsx`.

**How to avoid:**
Use an expand-migrate-contract rollout, not a destructive one-shot migration.
- First, define the v2 category contract with no group dependency in API schemas, frontend types, export format, seed logic, and reporting payloads.
- Add compatibility code that can read legacy grouped data while new code no longer requires groups.
- Backfill and verify all affected reads before removing tables, routes, fields, and locale strings.
- Explicitly decide whether backup import/export remains backward compatible with v1 archives and document the format version bump if not.

**Warning signs:**
- `rg "group_id|category_groups|group_name"` still returns hits in active API/schema/frontend files after the “migration is done”.
- Budgets page still needs `categoryGroups.list()` to render category options.
- `BudgetVsActual` or dashboard responses still expose group fields only because the UI has not been updated.
- Backup ZIPs still include `category_groups.json` with no migration story.

**Phase to address:**
Phase 1: Domain contract and compatibility design, before any destructive migration.

---

### Pitfall 2: Corrupting historical budget meaning during the category-owned budget migration

**What goes wrong:**
Budget editing becomes simpler in the UI, but historical month-specific and recurring semantics are lost. A user edits a category budget in April and unintentionally changes how January, February, and March should be interpreted, breaking reports and trust.

**Why it happens:**
Current budgets are not a single scalar on category. They are time-aware records with two behaviors: month-specific overrides and recurring defaults resolved by month in `backend/app/services/budget_service.py`. Replacing that with “category has optional budget” is only safe if v2 preserves time semantics explicitly.

**How to avoid:**
- Decide the canonical v2 budget model before building UI. If historical reporting stays monthly, the data model must still preserve effective dates or snapshots.
- Separate “category has a default budget” from “this month’s effective budget”.
- Migrate existing recurring and per-month budget rows into a representation that can reproduce past `budget vs actual` results exactly.
- Add fixture-based regression tests covering: recurring budget, override month, no-budget category, deleted category, and report comparison across adjacent months.
- In category edit flows, label whether the user is changing the default going forward, the current month only, or all historical periods. Do not infer silently.

**Warning signs:**
- Proposed schema removes `month` and `is_recurring` without a replacement concept.
- Dashboard totals for prior months change after editing a current category budget.
- Team language shifts from “effective budget for a month” to “the category’s budget” with no historical definition.
- Migration plan lacks before/after report parity checks for old data.

**Phase to address:**
Phase 2: Budget domain redesign and data migration.

---

### Pitfall 3: Moving budget creation into category forms without redesigning the surrounding workflows

**What goes wrong:**
The dedicated budgets page becomes inconsistent with category create/edit flows. Users can create a budget in one place but not see or manage recurrence/effective-month behavior the same way elsewhere. This creates duplicate logic, divergent validation, and inconsistent query invalidation.

**Why it happens:**
The current product has a standalone budgets route and dedicated CRUD API. Simply adding budget fields to category dialogs does not remove the need to reconcile dashboard, reports, budgets list, and any remaining mutations in `frontend/src/lib/api.ts` and `backend/app/api/budgets.py`.

**How to avoid:**
- Decide whether the budgets page remains as a reporting/management surface, becomes read-only, or is removed.
- Unify validation and mutation logic behind one service boundary so category forms and any remaining budget screens use the same server semantics.
- Define one UX for “budget enabled” categories and one UX for categories without budgets; do not keep hidden implicit defaults.
- Update query invalidation across category, budget, and dashboard keys as one change set.

**Warning signs:**
- Category edit supports budget amount, but recurring/effective behavior only exists on the old budgets page.
- Same budget can be changed from two screens using different payload shapes.
- Frontend types still treat `Category` and `Budget` as unrelated while the UI claims budgets are category-owned.
- Bugs appear where dashboard updates only after a hard refresh.

**Phase to address:**
Phase 3: Unified category and budget UX refactor.

---

### Pitfall 4: Shipping a cosmetic rebrand while leaving operational identity split between Securo and Flux

**What goes wrong:**
The UI says Flux, but backups, Docker project names, package metadata, installer output, browser title, config defaults, and test assertions still say Securo. Users read this as a broken or unfinished migration, and self-hosted operators may mis-handle backups or deployment assets.

**Why it happens:**
Rebrands are often scoped as text replacement in screens only. This repo still contains `Securo`/`securo` in public docs, compose names, config defaults, backup filenames, installer messages, locales, package metadata, and tests.

**How to avoid:**
- Inventory brand surfaces by layer: user UI, backup/export filenames, install scripts, container names, repo/docs metadata, environment defaults, and test fixtures.
- Decide which operational identifiers must remain stable for compatibility and which should change now.
- For changed identifiers, update both production code and tests in the same phase.
- Version backup/export naming deliberately so old archives remain recognizable and recoverable.
- Preserve existing API paths unless there is a strong reason to rename them; brand migration does not justify breaking URLs.

**Warning signs:**
- UI strings say Flux while downloaded file names still use `securo-backup-*`.
- `docker-compose*.yml`, `install.sh`, or `backend/app/core/config.py` still expose Securo after the rebrand phase is “done”.
- Test suite breaks mainly on expected strings and filenames after the rename.
- Docs or screenshots still instruct users to clone, run, or visit Securo-branded resources.

**Phase to address:**
Phase 1: Rebrand inventory and compatibility policy, then Phase 4: repo-wide rename execution.

---

### Pitfall 5: Breaking imports, exports, and automation by changing data contracts without versioning

**What goes wrong:**
Existing backups, import/export tooling, or future automation scripts stop working because category-group and budget payloads changed shape with no compatibility handling. The system appears fine in the SPA, but disaster recovery and data portability regress.

**Why it happens:**
Brownfield apps often validate only interactive UI flows. Here, backup/export already serializes raw entities, including `category_groups` and `budgets`, with a `format_version` field in `backend/app/api/export.py`. That is exactly where migration-sensitive breakage hides.

**How to avoid:**
- Treat backup/export format as a product contract.
- Introduce a new export format version for v2 if grouped categories disappear or budget ownership changes.
- Add import compatibility rules or a documented one-time conversion path for v1 exports.
- Test restore from a representative v1 archive into v2 before removing legacy tables or fields.
- Keep internal IDs stable through migration where possible; if IDs change, ship mapping logic and audit reports.

**Warning signs:**
- Migration plan mentions Alembic but not backup/restore verification.
- Export still dumps old entities even after the app no longer uses them.
- No fixture archive exists for a real v1 user with recurring budgets and grouped categories.
- Restore drills are postponed as “post-MVP”.

**Phase to address:**
Phase 2: Data migration and recovery compatibility.

---

### Pitfall 6: Under-testing report parity after budget/category model changes

**What goes wrong:**
Core finance screens remain green in unit tests, but spending-by-category, budget-vs-actual, and recurring projections drift subtly. Users notice only after historical numbers no longer match their expectations.

**Why it happens:**
Finance-app trust depends on stable historical outputs, not just successful writes. Current budget reporting mixes transactions, recurring projections, FX conversion, prior-month comparison, and category metadata in `backend/app/services/budget_service.py` and dashboard consumers in `frontend/src/pages/dashboard.tsx`.

**How to avoid:**
- Define parity fixtures from a real-world-like dataset before changing the model.
- Snapshot expected outputs for: monthly budgets list, budget-vs-actual API, dashboard category cards, and export contents.
- Verify both API-level and UI-level results for mobile layouts, since v2 keeps responsive web as a first-class constraint.
- Add regression cases for deleted groups, ungrouped categories, recurring budgets, override months, and categories with no budget.

**Warning signs:**
- Test plan covers category CRUD but not report parity.
- QA validates only “can create/edit category budget” flows.
- Product accepts changed numbers as “close enough” without explaining why.
- Frontend has no fixture or screenshot coverage for dashboard states with and without budgets.

**Phase to address:**
Phase 3: Regression harness and report parity verification, before rollout.

---

### Pitfall 7: Leaving legacy routes and schemas half-deprecated

**What goes wrong:**
The codebase ends up with dead or contradictory APIs such as `/api/category-groups`, `/api/budgets`, and category endpoints that all partially own budgeting. Future phases inherit ambiguity, and frontend/backend drift becomes the new normal.

**Why it happens:**
Teams often add the new path first and delay removal of the old path indefinitely. That is especially risky here because routers are thin and service boundaries are already direct imports; duplicate ownership spreads quickly.

**How to avoid:**
- Publish a deprecation matrix for every affected endpoint, schema, type, and query key.
- Mark one source of truth for category budgeting on the backend before updating the frontend.
- Keep overlap temporary and time-boxed: compatibility layer first, cleanup phase second.
- Delete dead routes, locale keys, frontend helpers, and service functions immediately after migration verification passes.

**Warning signs:**
- New category payload includes budget fields but `/api/budgets` remains writable indefinitely.
- Frontend still imports both `categoriesApi` and `budgetsApi` for the same interaction.
- Old routes remain because “they might be useful later”.
- Developers disagree on whether budget data should be fetched from categories or budgets.

**Phase to address:**
Phase 4: Contract cleanup and legacy removal.

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Keep `group_id` nullable in DB forever but pretend groups are gone in UI | Fast UI delivery | Hidden legacy dependency in reports, exports, and future features | Only during a short compatibility window |
| Store one `budget_amount` directly on category and ignore historical periods | Simpler forms and queries | Historical reports become wrong or undefined | Never if past-month reporting remains a product requirement |
| Leave both category-budget and budget CRUD flows active without deprecation policy | Lower migration pressure | Duplicate business rules and drift | Only for one planned overlap phase with explicit removal date |
| Rename visible strings only, skip scripts/config/tests | Faster demo-ready rebrand | Incomplete operational identity and support confusion | Never |
| Skip restore testing because export is “just a backup feature” | Saves time now | Highest-cost failure during real recovery | Never |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Alembic/PostgreSQL migration | Dropping old tables/columns in the same deploy that introduces new reads | Use expand -> backfill -> contract and verify data parity before removal |
| Frontend API client | Changing payload ownership without updating all query keys and consumers | Migrate client contracts centrally in `frontend/src/lib/api.ts` and remove conflicting reads together |
| Backup/export | Treating raw entity export as internal implementation detail | Version the export format and test restore from v1 data into v2 |
| i18n/locales | Renaming brand strings in one locale only, or leaving old marketing copy in auth/onboarding | Run a repo-wide brand inventory across all locale files and title/meta surfaces |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Dual-reading old and new budget sources for too long | Complex conditional logic, extra queries, hard-to-debug mismatches | Time-box compatibility reads and remove them after parity verification | Breaks developer velocity immediately; runtime cost is secondary |
| Recomputing report parity manually from UI tests only | Slow QA, missed edge cases in month resolution | Add backend fixture tests around report APIs plus a small UI smoke set | Breaks once historical edge cases accumulate |
| Running large backfills as one transaction | Long locks, migration rollback pain | Batch data migration and log counts before/after | Breaks on larger personal datasets or slower self-hosted hardware |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Logging raw backup/export payloads during migration debugging | Sensitive personal finance data exposure | Redact logs and use aggregate verification counts instead of payload dumps |
| Creating ad hoc admin migration endpoints for budget/category repair | New privileged attack surface | Keep migration logic in scripts/migrations, not exposed HTTP routes |
| Breaking auth-scoped ownership checks while rewriting services | Cross-user data leakage in a future multi-user path | Preserve `user_id` filtering and ownership tests on every migrated query |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Budget toggle silently creates/updates historical budget data | Users lose trust when past months change | Make the time effect explicit: current month, future months, or historical migration |
| Removing groups without improving category discoverability | Long category lists become harder to scan on mobile | Replace groups with strong sorting/filtering/search and clear budget-enabled markers |
| Rebrand changes names but not mental model copy | Product feels inconsistent and unfinished | Update backup labels, page titles, empty states, and onboarding/auth copy together |

## "Looks Done But Isn't" Checklist

- [ ] **Category migration:** No active API, schema, type, locale, export, or test still depends on `group_id` or `category_groups` without an explicit compatibility reason.
- [ ] **Budget ownership:** Editing a category budget does not alter prior-month report outputs unless the UX explicitly chose that scope.
- [ ] **Backup compatibility:** A v1 backup containing grouped categories and recurring budgets can be restored or converted in v2.
- [ ] **Rebrand:** Downloaded backups, browser title, installer output, docs, compose names, and config defaults all follow the chosen Flux naming policy.
- [ ] **Dashboard parity:** Budget-vs-actual and category spending match pre-migration outputs on fixture data.
- [ ] **Mobile UX:** Category create/edit with optional budgets remains usable on narrow screens without hiding critical controls.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Destructive group removal | HIGH | Restore DB snapshot, reintroduce compatibility reads, rerun migration with explicit mapping verification |
| Historical budget corruption | HIGH | Restore budget tables from backup, rebuild effective-month records, and rerun report parity checks before reopening edits |
| Incomplete rebrand | LOW | Finish remaining brand inventory, update tests/docs/scripts, and republish release notes clarifying unchanged operational identifiers |
| Export contract breakage | HIGH | Add versioned importer/converter for v1 archives and publish a recovery runbook |
| Report parity drift | MEDIUM | Freeze rollout, compare fixture outputs old vs new, and patch resolution logic before continuing |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Treating group removal as a simple column drop | Phase 1: Domain contract and compatibility design | No unresolved `group_id`/`category_groups` dependencies in active contracts; migration plan defines compatibility window |
| Corrupting historical budget meaning | Phase 2: Budget domain redesign and data migration | Fixture dataset reproduces old monthly budget results exactly after migration |
| Moving budget creation into category forms without workflow redesign | Phase 3: Unified category and budget UX refactor | Category and budget edits use one server contract and consistent UI semantics |
| Shipping a cosmetic rebrand only | Phase 1 and Phase 4 | Repo-wide brand audit passes across UI, scripts, config, tests, and backup filenames |
| Breaking imports/exports and automation | Phase 2 | Restore test from v1 archive into v2 passes or documented converter succeeds |
| Under-testing report parity | Phase 3 | Snapshot/API regression suite passes for budgets and dashboard outputs |
| Leaving legacy routes and schemas half-deprecated | Phase 4: Contract cleanup and legacy removal | Old routes/types/helpers are deleted and no duplicate ownership remains |

## Sources

- Internal codebase review:
  - `backend/app/models/category.py`
  - `backend/app/models/budget.py`
  - `backend/app/services/category_service.py`
  - `backend/app/services/category_group_service.py`
  - `backend/app/services/budget_service.py`
  - `backend/app/api/export.py`
  - `frontend/src/pages/budgets.tsx`
  - `frontend/src/lib/api.ts`
  - `frontend/src/types/index.ts`
- Prisma docs on expand/contract data migration pattern: https://docs.prisma.io/docs/guides/database/data-migration (MEDIUM confidence for migration pattern applicability; tooling differs, pattern transfers)
- SQLAlchemy cascade behavior docs: https://docs.sqlalchemy.org/20/orm/cascades.html (HIGH confidence for relationship-removal behavior)
- Kubernetes deprecation policy: https://kubernetes.io/docs/reference/using-api/deprecation-policy/ (MEDIUM confidence for compatibility-window guidance; analogous process lesson)
- Community evidence of budget-history trust failures: https://www.reddit.com/r/MonarchMoney/comments/1igrik4 (LOW confidence, but useful as user-trust signal)

---
*Pitfalls research for: self-hosted personal finance web app v2*
*Researched: 2026-04-03*
