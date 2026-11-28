# 0054. Per-account module limits on a managed instance

- Status: Accepted
- Date: 2026-11-28
- Origin: #1969 (the operator keeps some modules off for an account's workspaces)
- Extends: [0005](0005-modules-per-workspace.md), [0049](0049-hosted-usage-quotas.md)

## Context

Feature areas are modules (ADR 0005): `resolve_modules` in `backend/app/services/module_service.py` answers which ones a workspace has, the screens hide what it leaves out, and `require_module` / `require_module_write` in `backend/app/core/module_gate.py` refuse a route of a module the workspace does not have. On a managed instance (ADR 0045) the operator may need some modules to stay off for an account's workspaces, the same way ADR 0049 lets it hold an account to a set of numbers.

The repository is public, so what goes in it is only the mechanism: a neutral list of module ids per account, with no name attached and none of the operator's policy in the code.

## Decision

**A neutral list per account.** `user_module_limits` (`backend/app/models/user_module_limit.py`) holds, per user, the module ids its workspaces may use. A user with no row may use every module, so an account the operator never touched behaves as before. Ids outside `ModuleId` are refused when the list is written.

**It applies to the workspaces the user created**, by `created_by_user_id`, as the quota counts of ADR 0049 do. A workspace another account created and invited the user into keeps that account's modules.

**The list is copied onto the workspace row.** `Workspace.module_allowlist` holds the creator's list, written by `backend/app/services/module_limit_service.py` in the same transaction as the list itself (`set_limit`) and given to every workspace when it is created (`stamp_new_workspace`, from both creation paths in `backend/app/services/workspace_service.py`). `resolve_modules` is synchronous and runs on every gated request; reading the copy keeps it a pure function of the workspace row, with no query of its own.

**The resolver restricts, never grants.** A layer-3 provider, `AccountModuleLimitProvider`, intersects the resolved set with the list. A module the kind policy left out stays out whatever the list says, and the deployment veto still runs last. Every `require_module` gate and the `enabled_modules` the workspace answer carries follow without a route learning about it. Shared expense groups are already the `split_groups` module, so the list names them like any other.

**Only with `MANAGED_INSTANCE` on.** Off, the provider abstains and the copy is never read, which is the ADR 0004 rule. The copy is still kept current, so switching an instance to managed later enforces what the operator already wrote.

**Turning a module off keeps its data.** Nothing is deleted or rewritten: the module resolves as off and its routes answer `404`. Putting it back on the list brings everything back.

**Only the operator writes it**, through `GET` and `PUT /api/operator/modules/{user_id}` in `backend/app/api/operator.py`, guarded by `OPERATOR_TOKEN` like the quota routes: unset, they answer `404`; a wrong token, `403`. `PUT` with `modules: null` clears the list.

## Consequences

- A workspace's modules on a managed instance now depend on who created it. Transferring ownership through the members table does not move the list; `created_by_user_id` decides, as it does for the quotas.
- There is no per-workspace module toggle today (layer 3's `WorkspaceOverrideProvider` abstains), so there is nothing for a workspace owner to turn back on. When one is added, it must not reach past the account's list, and refusing it needs a readable code.
- Deleting a user deletes their list with them (`PERSONAL_TABLES` in `backend/app/services/user_removal_service.py`); the copies on their workspaces stay until the operator writes again.

## Enforced by

`backend/tests/test_account_module_limits.py`: no list is every module; the switch off reads nothing; a module off the list is off in an owned workspace and still on in a workspace another account invited the user into; the list never grants what the kind does not; a workspace created later takes the list; a gated route answers `404` and its data survives off and on; the operator route answers `404` without `OPERATOR_TOKEN` and `403` to an instance superuser.
