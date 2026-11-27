# 0002. The workspace is the data boundary

- Status: Accepted
- Date: 2026-05-27
- Origin: #227 (workspaces), #1435 (access tokens bound to their workspace)

## Context

Securo started with data owned by a user. Once a person could keep more than one set of books, and share one with other people, "the user's data" stopped being a meaningful filter: one user can belong to several workspaces, and one workspace can have several members.

## Decision

Every financial row belongs to a workspace, and every read and write is scoped to one.

- Financial tables carry `workspace_id`, a foreign key to `workspaces.id` with `ON DELETE CASCADE`, indexed.
- A request's workspace comes from the `X-Workspace-Id` header, resolved by `current_workspace` in `backend/app/core/workspace_context.py` into a `WorkspaceContext` (workspace, member, user). Without the header the user's default workspace is used (first non-archived one they belong to or manage). A workspace the user can't see answers 404, not 403, so its existence doesn't leak.
- Services filter by `ctx.id` explicitly.
- A request made with an access token is pinned to the token's workspace; naming another one answers 403. Rows shared from other workspaces (group expenses) are visible to session users but not to token requests: queries that pull them add `in_bound_workspace(...)` from `backend/app/core/shared_scope.py`.
- Tables without `workspace_id`, each for a reason:
  - global: `workspaces` itself, `app_settings` (instance settings), `fx_rates` (rates are facts, not anyone's data);
  - owned by a user across workspaces: `users`, `user_passkeys`, `agent_llm_connections` (a user's own LLM keys), `agent_llm_usage`;
  - scoped through a parent that has `workspace_id`, by a non-null foreign key that cascades: `institutions` (its bank connection), `collection_accounts` and `collection_asset_groups` (their collection), `agent_messages` (its conversation), `agent_tools`, `agent_knowledge_docs` and `agent_knowledge_chunks` (their agent).

`backend/app/core/workspace_autostamp.py` fills a missing `workspace_id` on insert from a parent row or the user's first workspace. It exists for backwards compatibility only and new code must not rely on it.

## Consequences

- A new table that holds financial data gets `workspace_id` like the others. A new global table needs a reason, written in its ADR or model docstring.
- A new route takes `WorkspaceContext` through `current_workspace` or `current_writable_workspace`, never a bare user.
- A query that crosses workspaces (shares) must think about access tokens.
- The frontend sends the header from `localStorage.workspace_id` and resets its whole query cache on a switch, so query keys don't carry the workspace.

## Enforced by

`tests/test_workspace_boundary_schema.py` walks every table: each one has a non-null, indexed `workspace_id` referencing `workspaces` with `ON DELETE CASCADE`, or is listed with one of the reasons above, and the reason is checked (a parent-scoped table really hangs off a workspace-owned parent). Per-area tests: `tests/test_asset_group_workspace_scope.py`, `tests/test_payee_workspace_scope.py`, `tests/test_access_tokens.py`, `tests/test_mcp_token_scoping.py`, `tests/test_group_visibility.py`, `tests/test_shared_transactions_view.py`.
