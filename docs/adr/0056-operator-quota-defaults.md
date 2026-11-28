# 0056. An account with no limits of its own follows the operator's defaults

- Status: Accepted
- Date: 2026-11-28
- Origin: #1986 (#1971)
- Extends: [0049](0049-hosted-usage-quotas.md)

## Context

ADR 0049 gives each account four neutral limits in `user_quotas`, written by the operator, and leaves an account with no row unbounded on all four. Every path that creates a user (sign-up, first OIDC login, an admin-created user, instance setup, the operator opening an account) would then have to write a row, and one that forgets hands out an unlimited account. The operator also had no way to move every account at once without rewriting each row.

ADR 0049 also left unsaid what happens to the Personal workspace created together with the account. Checking it against `max_personal_workspaces` would let a limit of 0 refuse the account itself, the first administrator's setup included, or leave a person with no workspace at all.

## Decision

**One set of defaults, written by the operator.** The operator sets the same four numbers at `/api/operator/quota-defaults` (`backend/app/api/operator.py`), behind the same `OPERATOR_TOKEN` as the per-account route. They are kept as an instance setting, `quota_defaults` in `app_settings`, which the customer's admin screen cannot read or write: `ALLOWED_SETTINGS` in `backend/app/api/admin.py` is an allowlist that does not name it, so its `PATCH` and `DELETE` answer `400`. A write is one upsert on the setting's key, so two first writes at once both succeed.

**Read at check time, never copied.** `effective_limits` in `backend/app/services/quota_service.py` answers the limits that hold and where they come from: the account's own row when it has one, the defaults when they are set, and unbounded otherwise. Nothing is copied into `user_quotas`, so every path that creates a user follows the defaults without writing anything, and changing them moves every account without a row at once. An account's own row always wins, even with all four counters null, so the operator can still exempt one account. With no defaults set, an account with no row is unbounded, as in ADR 0049.

**The bootstrap Personal workspace is never checked.** The Personal workspace created together with the account (`create_personal_workspace_for_user` in `backend/app/services/workspace_service.py`) is not checked against `max_personal_workspaces`. Once it exists it counts like any other personal workspace the user created, and the limit holds for the ones created after it.

Everything else in ADR 0049 holds as written: the switch, what is counted, the lock, the `quota_exceeded` answer, and that lowering a limit deletes nothing.

## Consequences

- On a managed instance an account with no row is unlimited only when the operator set no defaults. Setting them, or writing a row when the account is opened, is the operator's job.
- The operator's reads say where an account's limits come from (`own`, `default` or `none`), so a screen can tell an exempted account from one on the defaults.
- Lowering the defaults can leave many accounts above their numbers at once. As with a lowered row, nothing is removed and the next creation past the limit is refused.

## Enforced by

`backend/tests/test_quota_defaults.py`: no defaults means unbounded; the defaults hold a fresh sign-up and a first OIDC login; an account's own row ignores them; changing them moves accounts without a row and rewrites none; the switch off ignores them; only the operator writes them, and the admin screen's `PATCH` and `DELETE` refuse the key; a default below the count refuses and removes nothing. `backend/tests/test_quota_write_postgres.py` holds two first writes of the defaults at once.
