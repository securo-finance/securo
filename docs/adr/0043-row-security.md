# 0043. Postgres can hold the workspace boundary too

- Status: Proposed
- Date: 2026-11-25
- Origin: hardening for instances with people who don't know each other

## Context

The workspace boundary (ADR 0002) is a filter each query has to remember. Tests and review catch a missing one, but nothing stops it at run time, because the app connects as the owner of every table.

## Decision

- `ROW_SECURITY_ENABLED` (off by default, ADR 0004). Off, no statement is sent and every job runs as before.
- On, `DATABASE_URL` names a role that owns nothing and can't bypass row security. The owner keeps running migrations, then `python -m app.cli apply-row-security <role>`, which is safe to repeat and must run after each migration. It isn't a migration: the role's name belongs to the operator, and installs that keep it off change nothing. The API refuses to start when its role bypasses the policies or they are missing.
- Policies compare `workspace_id` with `app.workspace_id`. Without a workspace a covered table is empty. Every table with `workspace_id` is covered except the ones `NOT_COVERED` lists with a reason; tables scoped by a parent follow it.
- The workspace lives on the session and is sent each time a transaction begins, so it survives a commit inside a request and never stays on a pooled connection. `current_workspace`, the MCP `call_tool` and workspace creation bind it.
- A job never reads across workspaces. `workspace_makers` gives a job that covers all of them one session maker per workspace, and `maker_for_row` finds the workspace of a row given by id through a function that answers only that.
- A transaction shared with a person from another workspace is readable, never writable, through a second policy that repeats `viewer_member_predicate`. An access token gets none.

## Not covered yet

- `workspace_members`, `access_tokens`, `groups`, `group_members`, `group_settlements` and `transaction_splits` have no policy. User-owned and instance tables (ADR 0002) have none either.
- Requests without a workspace still need one before the switch can be turned on: shared invoice links, deleting a user, and the restamp after someone changes their display currency, which spans every workspace they have rows in. Until then they find nothing in covered tables, or only the bound workspace's rows. Admin and sign-in routes have no test under the role yet.
- The account and category of a transaction shared from another workspace are not visible, and settlements that write there are refused.
- Compose and Helm don't create the role or give the migrate job a URL of its own.

## Consequences

- It guards against a forgotten filter, not against someone who can run SQL as the role: they can set the workspace themselves.
- `SECRET_KEY` must be set, because the restricted role can't read or create the stored one.
- Jobs open one session per workspace, and prices shared by several workspaces are fetched once for each.

## Enforced by

`backend/tests/test_row_security_postgres.py`, as a real restricted role: every covered table read, inserted, updated and deleted across workspaces, the context across commits and the pool, each scheduled job, shared transactions, startup, and a sign-up to transactions pass over HTTP. A new table with `workspace_id` is covered unless listed.
