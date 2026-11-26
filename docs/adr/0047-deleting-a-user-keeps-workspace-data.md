# 0047. Deleting a user keeps what they added to other people's workspaces

- Status: Accepted
- Date: 2026-11-25
- Origin: #1896

## Context

ADR 0002 makes the workspace the data boundary. Deleting a user predates it and deleted by `user_id`, which only records who wrote a row. In a shared workspace those rows belong to its owner.

The author column is required on most financial tables. It also feeds the display currency of a stamp, the payer of a group expense and several unique constraints, so handing the rows to another person would change balances and conversions.

## Decision

1. **A deletion takes the person's own rows and the workspaces only they reach.** A workspace with another owner or manager is kept. One that has other members and no other owner refuses the deletion until somebody is made an owner or the members are removed.
2. **Everything else in other workspaces is kept.** A nullable author is cleared by its foreign key.
3. **A required author keeps the `users` row, closed.** The row becomes inactive and loses its address, password, second factor, sign-in provider, theme and preferences, except the display currency that stored conversions read. The original address is free for a new account. Nothing lists or edits a closed row.
4. **Every required reference to a user is classified** as personal (deleted) or authored (kept), in one place.
5. **A workspace is deleted by its own row**: each `workspace_id` foreign key cascades.

## Consequences

- A closed row stays until the rows it wrote are gone. It can't sign in.
- Bank connections a removed editor set up belong to the workspace and keep syncing until its owner removes them.
- A group member linked to a closed row stays linked, so the debts of the group don't move.
- Exporting a workspace before it is deleted is not part of this decision.
- Files of deleted attachments stay in storage, as before.

## Enforced by

- `backend/app/services/user_removal_service.py`, called by `delete_user` in `backend/app/services/admin_service.py`.
- `backend/tests/test_delete_user_keeps_workspace_data_postgres.py`, which also fails on an unclassified table or a `workspace_id` that doesn't cascade.
