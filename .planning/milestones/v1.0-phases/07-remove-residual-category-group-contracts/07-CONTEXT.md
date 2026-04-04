# Phase 7: Remove Residual Category Group Contracts - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Close the remaining milestone audit gaps by removing category-group dependency from the supported category API contract and default setup/category seeding flow, while preserving the existing flat category UX and current budget/report/export behavior.

</domain>

<decisions>
## Implementation Decisions

### Supported Category Contract
- The supported `/api/categories` create, update, and read contract should no longer expose `group_id`.
- The frontend shared `Category` type should match that flat contract so compile-time drift cannot reintroduce group usage.

### Setup and Default Data
- Default category seeding should create flat system categories directly and must not call default category-group creation anymore.
- This phase should remove active runtime dependence on category groups, not attempt a full schema/table deletion unless required to satisfy the phase.

### Compatibility Boundary
- Preserve current category-owned budget behavior and mobile category flows unchanged except where contract cleanup requires small type updates.
- Keep export compatibility and any dormant legacy storage paths unless they are part of an active supported flow blocked by the milestone audit.

### the agent's Discretion
- Exact refactor shape inside `category_service.py`.
- Test coverage split between service, API, and setup flows.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Milestone and gap definition
- `.planning/ROADMAP.md` — Phase 7 goal, requirements, and success criteria
- `.planning/REQUIREMENTS.md` — Active REQ IDs reassigned to Phase 7
- `.planning/v1.0-MILESTONE-AUDIT.md` — Requirement and integration gaps this phase must close

### Prior cleanup decisions
- `.planning/phases/05-legacy-removal-and-release-cleanup/05-CONTEXT.md` — prior decision to remove public legacy paths after migration
- `.planning/phases/06-remover-a-fun-o-or-amentos-do-menu-e-sua-tela-tamb-m/06-CONTEXT.md` — confirms Categories remains the supported budget flow

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `backend/app/api/categories.py` already centralizes the supported category CRUD contract.
- `backend/app/services/category_service.py` already owns default category seeding and budget-state normalization.
- `backend/tests/test_category_service.py`, `backend/tests/test_categories_api.py`, and `backend/tests/test_setup_api.py` cover the affected flows directly.

### Established Patterns
- Category budget state is normalized in the service layer from schema payloads rather than in the API router.
- The frontend relies on the shared `frontend/src/types/index.ts` contract and catches API shape drift via the production build.

### Integration Points
- `/api/setup/create-admin` depends on `create_default_categories()`, so setup behavior changes land through the category service.
- `frontend/src/pages/categories.tsx` and other readers consume the shared `Category` type, so removing `group_id` must remain type-safe across the app.

</code_context>

<specifics>
## Specific Ideas

- Use the milestone audit as the scope guard: fix the active contract and runtime seed path first.
- Prefer targeted cleanup over a broad legacy-table removal that could spill into export compatibility work.

</specifics>

<deferred>
## Deferred Ideas

- Full removal of dormant `category_groups` models, services, migrations, or export compatibility data if they remain outside active supported flows after this phase.

</deferred>
