# 0049. Per-account usage limits on a managed instance

- Status: Accepted
- Date: 2026-11-27
- Origin: #1959 (a ceiling per account on a managed instance with open sign-up)
- Extends: [0045](0045-managed-instance.md)

## Context

Securo runs self-hosted, where the person operating the instance is the person using it, and since ADR 0045 it can also run as a managed instance, where one person or team operates it for others. In the first case a per-account limit means nothing. In the second, an account can create workspaces, invite people and open bank connections, and whoever runs the instance may want each account to stay within a set of numbers: every one of those resources takes something from the instance — compute, storage, an outside connection, someone's attention.

The repository is public. What goes in it is therefore only the ceiling: four neutral numbers per account, with no name attached to them and none of the operator's own policy in the code. What the operator ties those numbers to is decided and kept outside this repository.

ADR 0045 already separates the operator from the customer with one switch, `MANAGED_INSTANCE`, and ADR 0004 keeps optional things to one switch, off by default and free when off. The quota rides on that switch instead of adding a second one.

## Decision

**This records the decision; the code comes with #1964.** Until #1964 is in the tree there is no `user_quotas`, no operator route and no check, so a managed instance enforces nothing. The rules below are what holds once it is in.

**Four neutral numbers, nothing else.** The code keeps a table of limits per user — `user_quotas`: `user_id`, `max_personal_workspaces`, `max_business_workspaces`, `max_bank_connections`, `max_members_per_workspace`. The code attaches no meaning to them beyond the count it makes. A null counter means unbounded for that one, and a user with no row is unbounded on all four.

**It only serves the managed instance.** Checks run only when `MANAGED_INSTANCE` is on (`managed_instance` in `backend/app/core/config.py`, ADR 0045), documented in `.env.example` beside the flag. Off, no code path reads the table and a self-hosted instance behaves exactly as today, which is the ADR 0004 rule.

**What is counted is ownership, not membership:**

- Creating a workspace (`create_workspace` in `backend/app/services/workspace_service.py`) counts the workspaces of the same `kind` (`personal` or `business`, in `backend/app/models/workspace.py`) that the user created, by `created_by_user_id`. A workspace someone was invited into never fills a number of theirs.
- Creating a bank connection counts the `BankConnection` rows (`backend/app/models/bank_connection.py`) across every workspace the user created, in both paths that can create one: the native one in `backend/app/services/connection_service.py` and the plugin one (`_add_bank` in `backend/app/plugins/connections.py`). Connections, not accounts: one connection brings several accounts and it is the connection that is opened and renewed, while accounts typed in by hand are free.
- Adding a member (`add_member` in `backend/app/services/workspace_service.py`, and the invite path once it is on main) counts the members of the workspaces the user created and refuses past `max_members_per_workspace`.

All three counts leave archived workspaces out: closing a workspace (`archive_workspace` in `backend/app/services/workspace_service.py`) gives its place back, and the bank connections inside a closed workspace stop counting, so a person at a limit can close one workspace and open another. Every count reads `Workspace.is_archived` rather than a stored counter, which is why the row keeps existing while the place is free.

**Over a limit**, the route answers `403` with `detail.code = "quota_exceeded"`, naming the counter that filled and its current count and maximum, so a screen can say what happened. The response carries the counter, its count and its maximum, and nothing the operator did not give it as a number.

**The connection count needs one narrow definer function.** `bank_connections` is a covered table — its policy is `workspace_id = app.workspace_id` — so with row security on a request sees only the workspace it is bound to (`bind_workspace`), and a count across all the workspaces a user created would come back short: the ceiling could be dodged by spreading connections over several workspaces. The count therefore gets its own function, `securo_owned_bank_connections(p_user uuid) RETURNS bigint`, built in `setup_statements` in `backend/app/core/row_security.py` the way `securo_row_workspace` and `securo_shared_with_viewer` already are: `STABLE SECURITY DEFINER`, fixed `search_path = pg_catalog`, revoked from `PUBLIC`, granted to the restricted role only, and answering one number and nothing else. With row security off it is an ordinary `SELECT`. The other two counts need no function: `workspaces` carries no `workspace_id`, and `workspace_members` is listed in `NOT_COVERED`, so both are readable whole.

**Only the operator writes the numbers.** A route outside the customer's admin — `/api/operator/quotas`, guarded by the `OPERATOR_TOKEN` setting (unset means the route answers `404`) — upserts a user's four numbers. It cannot be the instance superuser's route: on a managed instance the customer's administrator is a superuser (ADR 0045 says so), and these numbers are the operator's.

**Lowering the limits deletes nothing and never refuses.** The route writes the four numbers as they come, and an account already above them keeps working; refusing there would leave the operator unable to bring an account down to the numbers it should have had. `check_fit(user_id, target)` answers, per counter, the current count, the target and the excess, so the operator sees what removal a lower number implies before writing it, and the answer is what a screen would need to say "remove one workspace and two connections". Until the account fits, nothing is forced: the person removes the excess themselves — deleting a workspace, deleting a connection, leaving or removing a member — and the count comes back under the number on its own. Choosing what to keep stays with the person, so nothing has to guess.

**Left open, and not to be invented by the code:** an account that has to come down to lower limits without the person acting has no rule here. What happens to the excess then — read-only, sync paused, or a grace period — is a product decision, not this code's.

## Consequences

- The connection count now crosses workspaces on purpose, from inside a definer function that returns a number and nothing else. That is a new privileged read: it is granted to the restricted role alone, its body names one column of one table, and it must never grow into a general lookup — the moment it returns rows instead of a count, it is a hole in the data boundary, not a quota.
- `workspace_members` and `workspaces` are read whole, as ADR 0045 and the row-security work already accept. The quota reads them only to answer the caller's own count, and it is not a reason to leave either one out of the policy.
- On a managed instance a user with no row is unlimited. Writing the row when the account is opened is the operator's job, and a sign-up path that forgets it hands out an unlimited account.
- An invited member occupies a seat in the owner's number and frees it when they leave. The invited person's own workspace numbers never count workspaces they were invited into.
- Deleting a user (ADR 0047) or closing a workspace gives the seats back for free, because every count is derived from the rows and never stored as a counter.
- Changing what an account may hold is a rewrite of four numbers by the operator: no release, and nothing in the app needs to learn why the numbers changed.
- The backup on demand (`POST /api/backups/run`) is not one of these counters. It stays without a per-account limit until it is decided which counter it becomes.

## Enforced by

#1964, which adds the table, the gates and the operator route, brings the tests in new modules under `backend/tests/`: the switch off enforces nothing; each counter refuses at its own gate; a workspace someone was invited into never counts; connections are counted across every workspace the user created and in both creation paths, while manual accounts are free; `check_fit` reports the excess per counter and writes nothing; and the operator route answers `404` without `OPERATOR_TOKEN` and `403` to an instance superuser. The count and the insert that follows it hold one lock, keyed by the account (workspaces and connections) or by the workspace (members), so two requests that arrive together cannot both pass the last free place; the lock is taken before the count, one at a time and never nested, and it is not taken at all when the quota does not apply.
