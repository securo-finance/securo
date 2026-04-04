# Phase 5: Legacy Removal and Release Cleanup - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Now that readers and UI flows have migrated, remove the remaining public legacy category-group and standalone budget surfaces, and align release/setup naming to Flux wherever the old repository or container identifiers are not required for compatibility.

</domain>

<decisions>
## Implementation Decisions

### Legacy Path Removal
- Public category-group editing routes should be removed once the frontend no longer depends on them.
- Standalone budget CRUD routes should be removed once category forms own budget editing and budget pages only read current category-owned limits.

### Release Cleanup
- User-facing product/setup/docs text should say `Flux`.
- Legacy repository URLs, package names, and low-level compose/database identifiers may remain where changing them would be a compatibility or infrastructure concern.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/src/pages/budgets.tsx` can read category-owned budget state directly from categories.
- `backend/app/api/budgets.py` can be reduced to the comparison reader once standalone CRUD routes are no longer used.
- `backend/app/main.py` is the single place to stop exposing category-group routes publicly.

### Integration Points
- `frontend/src/lib/api.ts` still carries the last frontend bindings for legacy group and budget CRUD paths.
- `README.md`, `install.sh`, `CONTRIBUTING.md`, and `SECURITY.md` still contain user-facing Securo naming.

</code_context>
