# 0049. A suspended workspace is read and exported, never written

- Status: Accepted
- Date: 2026-11-25
- Extends: [0003](0003-workspace-roles-and-write-gates.md)

## Context

The administrator of an instance with several workspaces had no way to stop one without deleting it or disabling its people. Archiving is the owner's choice and hides the workspace from its own members, and disabling a user also locks them out of every other workspace they belong to.

## Decision

- `workspaces.status` is `active` or `suspended` (`WorkspaceStatus` in `backend/app/models/workspace.py`), with `status_changed_at`. Only an instance administrator changes it, through `/api/admin/workspaces`. It is separate from `is_archived`.
- `WorkspaceContext.require_write()` refuses every role, the owner included, with a 403 whose text says why. The role is checked first, so a viewer still gets the read-only message. MCP tools that apply changes follow the same rule.
- The workspace routes that check ownership inside the handler refuse a rename, a new member and a role change. Leaving, removing a member and archiving stay open.
- A write that starts in another workspace and lands here, a linked member's settlement, is refused as well.
- Reading and every export keep working, for the same people as before.
- Scheduled jobs that write (bank sync, plugin sync and routines, recurring transactions, invoice schedules, asset prices and growth) leave suspended workspaces out with `in_active_workspace()`, or check `is_suspended()` when they act on one row. A job already queued checks again when it runs.

## Consequences

- A new scheduled job that writes workspace data adds one of the two. Nothing forces it: the write gate covers routes, not jobs.
- Reactivating resumes the schedules where they stopped, so recurring transactions and invoice schedules catch up on the periods they missed.
- FX restamps and scheduled backups keep running: the first keeps what is read right, the second is an export.
- The status belongs to the workspace. Its owner can still use, or create, another one.

## Enforced by

`backend/tests/test_workspace_status.py`: each transition, reads and export while suspended, and each job with the workspace suspended and active.
