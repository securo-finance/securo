# 0003. Workspace roles, and a gate on every write route

- Status: Accepted
- Date: 2026-05-27
- Origin: #227 (roles), #613 (write gates and their meta-test)

## Context

A shared workspace needs people who can read without changing anything. A role check that each route has to remember is a role check some route will forget: an audit before #613 found mutating routes reachable by viewers.

## Decision

- Members have one of three roles, `owner`, `editor` or `viewer` (`WORKSPACE_ROLES` in `backend/app/models/workspace.py`). A user named in `Workspace.managed_by_user_id` acts with owner rights without being a member: the virtual `manager` role (`MANAGER_VIRTUAL_ROLE`).
- `WorkspaceContext` answers `can_write` (owner, editor, manager) and `is_owner` (owner, manager); `require_write()` raises 403.
- Every POST, PUT, PATCH and DELETE route reaches a recognized write gate somewhere in its dependency graph: today `current_writable_workspace`, directly or through a wrapper such as `require_module_write`. The set of gates is `WRITE_GATES` in `tests/test_write_permission_coverage.py`. Routes that don't take this gate are listed in its `ALLOWLIST`, each with its reason. The kinds of reason today: routes about the user rather than a workspace (their own credentials, login); instance administration behind `current_superuser`; workspace routes that check ownership inside the handler; and workspace POSTs a viewer may call because they write nothing shared (import previews, which parse an upload and return it; the viewer's own widget layout; access tokens, capped at the minter's role). The test file is the source of truth for the list.
- Owner-only actions check ownership inside the handler: renaming or archiving a workspace and managing its members (`backend/app/api/workspaces.py`, allowlisted as "owner floor inside the handler"), installing code plugins. `WorkspaceContext.require_owner()` exists but nothing calls it yet.

## Consequences

- A new mutating route without a gate fails CI. If a new kind of gate is needed, it is added to `WRITE_GATES`; the assertion is never loosened. A route added to `ALLOWLIST` carries its reason. The test checks that the reason is there, not that the handler really checks ownership: an allowlisted owner route needs its own test.
- Roles are about permission. Whether a feature is present at all is a different question, answered by modules (ADR 0005), and absent modules answer 404.

## Enforced by

`tests/test_write_permission_coverage.py` walks every mutating route's dependency graph and fails when no gate is found. `tests/test_write_permission_gate.py` checks a viewer gets 403 over HTTP. The contract sweep in `tests/api_contract/` runs every route as a viewer (`test_contract_viewer_*`).
