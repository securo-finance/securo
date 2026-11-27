# 0049. Usage limits belong to the account, and the code knows nothing about plans

- Status: Accepted
- Date: 2026-11-27
- Origin: decision by Tássio on 2026-11-27 for the hosted product's plan tiers; board task #546

## Context

The hosted Securo will be sold in plans, and the same code also runs self-hosted, where the person running the instance is the person using it and a limit makes no sense. Two things follow.

The repository is public. Plan names, numbers and prices are commercial, change often and are not ours to publish: a price change would need a release, and a plan name in the code is a plan name in everybody's instance. An open sign-up, on the other hand, needs a ceiling: a bank connection costs money per connection (Pluggy bills connections, and one connection brings several accounts), and workspaces and members cost CPU, storage and support.

ADR 0045 already separates the operator from the customer with one switch, `MANAGED_INSTANCE`, and ADR 0004 keeps optional things to one switch, off by default and free when off. The quota rides on that switch instead of adding a second one.

## Decision

**One neutral quota, no plans.** The code keeps a table of limits per user — `user_quotas`: `user_id`, `max_personal_workspaces`, `max_business_workspaces`, `max_bank_connections`, `max_members_per_workspace`. No plan id, no plan name, no price, and no such string in the repository. The plan catalog and the billing live in the operator's private repository, which writes these four numbers. A null counter means unbounded for that one.

**It only serves the managed instance.** Checks run only when `MANAGED_INSTANCE` is on (`managed_instance` in `backend/app/core/config.py`, ADR 0045), documented in `.env.example` beside the flag. Off, no code path reads the table and a self-hosted instance behaves exactly as today, which is the ADR 0004 rule.

**What is counted is ownership, not membership:**

- Creating a workspace (`create_workspace` in `backend/app/services/workspace_service.py`) counts the workspaces of the same `kind` (`personal` or `business`, in `backend/app/models/workspace.py`) that the user created, by `created_by_user_id`. A workspace someone was invited into never fills a number of theirs.
- Creating a bank connection counts the `BankConnection` rows (`backend/app/models/bank_connection.py`) across every workspace the user created, in both paths that can create one: the native one in `backend/app/services/connection_service.py` and the plugin one (`_add_bank` in `backend/app/plugins/connections.py`). Connections, not accounts: one Pluggy connection brings several accounts and it is the connection we pay for. Accounts typed in by hand are free.
- Adding a member (`add_member` in `backend/app/services/workspace_service.py`, and the invite path of #542) counts the members of the workspaces the user created and refuses past `max_members_per_workspace`.

**Over a limit**, the route answers `403` with `detail.code = "quota_exceeded"`, naming the counter that filled and its current count and maximum, so the screen can say what happened and offer the upgrade link. The link is the instance setting `upgrade_url` (`ALLOWED_SETTINGS` in `backend/app/api/admin.py`), which on a managed instance is the operator's, per ADR 0045. The app never shows a plan name or a price it was not given as a number.

**The connection count needs one narrow definer function.** `bank_connections` is a covered table — its policy is `workspace_id = app.workspace_id` — so with row security on a request sees only the workspace it is bound to (`bind_workspace`), and a count across all the workspaces a user created would come back short: the ceiling could be dodged by spreading connections over several workspaces. The count therefore gets its own function, `securo_owned_bank_connections(p_user uuid) RETURNS bigint`, built in `setup_statements` in `backend/app/core/row_security.py` the way `securo_row_workspace` and `securo_shared_with_viewer` already are: `STABLE SECURITY DEFINER`, fixed `search_path = pg_catalog`, revoked from `PUBLIC`, granted to the restricted role only, and answering one number and nothing else. With row security off it is an ordinary `SELECT`. The other two counts need no function: `workspaces` carries no `workspace_id`, and `workspace_members` is listed in `NOT_COVERED`, so both are readable whole.

**Only the operator writes the numbers.** A route outside the customer's admin — `/api/operator/quotas`, guarded by the `OPERATOR_TOKEN` setting (unset means the route answers `404`) — upserts a user's four numbers. It cannot be the instance superuser's route: on a hosted instance the customer's administrator is a superuser (ADR 0045 says so), and these numbers are ours.

**A downgrade is blocked until the account fits** (Tássio, 2026-11-27). A downgrade deletes nothing and never leaves an account above the plan it is on. `check_downgrade(user_id, target)` answers, per counter, the current count, the target and the resources over it, so billing can refuse the change and the screen can say "remove one workspace and two connections to switch". The person removes the excess themselves — deleting a workspace, deleting a connection, leaving or removing a member — and the downgrade goes through once every count fits. The user chooses what to keep, and nothing has to guess.

**Left open, and not to be invented by the code:** an involuntary downgrade (a payment fails, the subscription expires) cannot wait for the person to remove anything. What happens to the excess then — read-only, sync paused, or a grace period — is Tássio's decision; board task #546 carries it open.

## Consequences

- The connection count now crosses workspaces on purpose, from inside a definer function that returns a number and nothing else. That is a new privileged read: it is granted to the restricted role alone, its body names one column of one table, and it must never grow into a general lookup — the moment it returns rows instead of a count, it is a hole in the data boundary, not a quota.
- `workspace_members` and `workspaces` are read whole, as ADR 0045 and the row-security work already accept. The quota reads them only to answer the caller's own count, and it is not a reason to leave either one out of the policy.
- On a managed instance a user with no row is unlimited. Writing the row when the account is opened is the operator's job, and a sign-up path that forgets it hands out an unlimited account.
- An invited member occupies a seat in the owner's number and frees it when they leave. The invited person's own workspace numbers never count workspaces they were invited into.
- Deleting a user or closing a workspace (#543, #580) gives the seats back for free, because every count is derived from the rows and never stored as a counter.
- A plan change is a rewrite of four numbers by the operator: no release, and nothing in the app needs to learn that a plan exists.
- The backup on demand (`POST /api/backups/run`) is not one of these counters. It stays without a per-account limit until it is decided which counter it becomes.

## Enforced by

Nothing yet: this ADR records the decision before the code. The pull request that adds the table and the checks brings the tests, in a new module under `backend/tests/`: the switch off enforces nothing; each counter refuses at its own gate; a workspace someone was invited into never counts; connections are counted across every workspace the user created and in both creation paths, while manual accounts are free; `check_downgrade` reports the excess per counter and refuses while it is above zero; and the operator route answers `404` without `OPERATOR_TOKEN` and `403` to an instance superuser.
