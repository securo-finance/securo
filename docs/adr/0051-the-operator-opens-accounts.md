# 0051. The operator of a managed instance opens an account in one call, with no password

- Status: Accepted
- Date: 2026-11-27
- Origin: #1965
- Extends: [0042](0042-admin-actions-on-accounts-leave-a-record.md), [0045](0045-managed-instance.md), ADR 0049 (per-account limits)

## Context

On a managed instance the operator writes each account's limits (ADR 0049), but only for an account that already exists. An account is born from public sign-up or from an administrator typing an initial password, and ADR 0042 says a password belongs to the person it authenticates. Sign-up should be able to stay closed on a managed instance, and no account should ever exist without its limits.

## Decision

**One route, one transaction.** `POST /api/operator/accounts` in `backend/app/api/operator.py` takes the email, the operator's `external_reference`, the account's language, the workspaces and the limits. `operator_account_service.open_account` in `backend/app/services/operator_account_service.py` writes the user, the limits row when the call sends limits, each workspace (seeded as `workspace_service.create_workspace` seeds one from the app, plus the wallet a new account starts with, and for a business its legal name, jurisdiction and tax ids) and the record, then commits once. The limits are written before the workspaces, so the workspaces are held to them: asking for more than the limits allow opens nothing. A call that leaves the limits out writes no row, on purpose: an account's own row wins over the operator's defaults (#1971), so a row of nulls would leave it unbounded, while no row holds it to the defaults. Limits sent as four nulls are written as the account's own, which is how the operator exempts one account. The seeding helpers take `commit=False` on this path so the transaction stays whole.

**The account has no password anyone knows.** The column cannot be empty, so it holds the hash of a secret thrown away at once, the same shape the account removal shell uses. No password signs in to it until the person sets one of their own (#1967). The account is not marked verified: the operator typed the address, the person has not shown they read it.

**Whether a password was set is its own column.** Since the password column is never empty, it cannot say whether the person has set one. `users.password_set_at` (migration 137) is null on the operator's accounts, on accounts an identity provider created (they hold a throwaway too) and on removal shells. Every path that writes a password the person typed fills it: the sign-ups through the user manager (register, setup, admin, invite) are stamped by its adapter in `backend/app/core/auth.py`, the CLI stamps its own, and `UserManager._update` stamps a password changed or reset, which is how a link that sets the first password (#1967) fills it. The column has no default on purpose: SQLAlchemy applies a column default over an explicit null. The migration stamps existing accounts, except removal shells and accounts with an identity provider in their preferences.

**The reference makes a retry safe.** `users.external_reference` (migration 137) is unique and the code attaches no meaning to it. The same reference with the same address answers the account it already opened, with `200` and `created: false`, and changes nothing even if the body changed. The same reference with another address is refused (`external_reference_taken`), and an address that already has an account is refused (`email_taken`); neither is ever merged into an existing account. Two parallel calls meet at the unique indexes, and the loser answers what the winner opened.

**The operator is the actor in the record.** The row in `admin_access_log` has `action` `account.open`, no `actor_id` and `actor_email` `operator`, the new account as target, and `changes` naming the reference, the workspaces and the limits. The person whose account it is can read it.

**Off, it does not exist.** With `MANAGED_INSTANCE` off the route answers `404` whatever the token (ADR 0004); without `OPERATOR_TOKEN` it answers `404`; any other bearer, an instance superuser's session or an access token included, answers `403`.

## Consequences

- An account opened this way cannot be used until the person sets a password; until #1967 the operator has no way to hand that step over.
- Deleting an account clears its reference with the other nullable columns, so the reference can open a new account afterwards.
- The response carries the limits as stored, so the operator can check what it wrote without a second call.

## Enforced by

`backend/tests/test_operator_accounts.py` (the switch off, no token, a superuser, an access token, a first call, the same call again, a reference reused for another address, a taken email, a business workspace with its legal fields, an invalid tax id, the limits in the same transaction, no row when the limits are left out and a row when they are sent as nulls, workspaces over the limits, the record and its viewer, no password set until the person sets one), `backend/tests/test_password_set_at.py` (sign-up, identity provider, password change, an update without a password, removal shell), `backend/tests/test_password_reset_email.py` (a recovery link stamps it) and `backend/tests/test_operator_accounts_postgres.py` (the same route under row security, with several workspaces in one transaction).
