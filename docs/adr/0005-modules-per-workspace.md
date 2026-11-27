# 0005. Modules are resolved per workspace

- Status: Accepted
- Date: 2026-08-12
- Origin: #569 (workspace kind chosen at creation), #571 (module resolver)

## Context

Not every workspace needs every screen. A household budget has no use for invoices; a shared trip has no use for assets. Deployment flags (ADR 0004) are per instance, so they can't express this.

## Decision

- The app is divided into modules, listed in `ModuleId` in `backend/app/services/module_service.py` and mirrored by `MODULE_IDS` in `frontend/src/lib/modules.ts`.
- `resolve_modules` decides which are on for a workspace through a chain: deployment flags can veto, then the catalog, then the policy for the workspace's kind (`KIND_POLICIES`), then the workspace's own overrides.
- The workspace kind is chosen at creation and never edited (`WorkspaceKind`, migration `068_workspace_kind_normalize.py`).
- Routes of a module depend on `require_module` or `require_module_write` (`backend/app/core/module_gate.py`); an absent module answers 404. The frontend hides it through `module-route.tsx` and `visibleNavItems`.

## Consequences

- A new feature area with its own screens becomes a module, added in both lists.
- Module gates are not permissions. A route that writes still needs a write gate (ADR 0003); `require_module_write` does both.

## Enforced by

`tests/test_module_service.py`, `tests/test_module_gate.py`, `tests/test_workspace_kind.py`, and `frontend/src/lib/nav-items.test.ts`. Nothing checks that the backend and frontend module lists match.
