# 0049. A workspace can use its own provider account, set by an instance admin

- Status: Proposed
- Date: 2026-11-26
- Origin: #1945 (issue #1351)

## Context

Provider credentials are instance settings ([0006](0006-bank-sync-providers.md), stored as in #1033), so every workspace's bank connections land in one provider account. People who share an instance but not a bank account, such as a family, each need their own: their connections count against their own plan and stay out of someone else's dashboard.

Some providers keep a connection only under the account that created it. A Pluggy item belongs to one Pluggy client, and an Enable Banking session to one application. Moving a workspace to another account would leave its connections unreachable.

## Decision

- `workspace_provider_credentials` (`backend/app/models/workspace_provider_credential.py`) holds a workspace's values, encrypted like the instance ones and never returned. `resolve_settings(session, workspace_id)` in `backend/app/services/provider_settings.py` lays them over the instance settings. Every path that builds a provider for a workspace passes it: connect and reconnect tokens, institutions, OAuth, reauth, sync, holdings and trades.
- A workspace without its own values uses the instance account. A saved value that can't be decrypted makes the provider unavailable there. It never falls back to the instance account.
- Which providers take workspace values is `WORKSPACE_PROVIDERS` in `provider_settings.py`: the fields, and the field that names the account connections are created under. Only Pluggy is listed. The provider status returns `workspace_fields`, so the admin screen needs only texts for a new one.
- A change that would move connections to another account is refused with `409 provider_account_change`. That covers a workspace given another account, a workspace's values removed when the instance account differs, and the instance account changed while workspaces without their own values have connections. A new secret for the same account is allowed. So is replacing a value that can't be read, so the admin can recover.
- These checks read what the other changes write, so they run one at a time: `lock_provider_accounts` takes a transaction lock per provider (`pg_advisory_xact_lock`) before anything is read, in the instance change, the workspace set and the workspace remove. The OAuth callback takes the same lock and checks, through `ensure_account_unchanged`, that the workspace is still on the account the connection was made under before saving it, and holds the lock until the connection commits.
- Only an instance admin sets them, under `/api/admin/provider-settings/{provider}/workspaces`. Members of the workspace don't handle provider secrets. On a managed instance ([0045](0045-managed-instance.md)) they are read-only, like the instance credentials.
- The table is covered by row security ([0043](0043-row-security.md)). Admin routes bind no workspace, so the service binds the workspace it writes for, and reads the admin list one workspace at a time. With row security on, `resolve_settings` refuses a session bound to another workspace or none, which would otherwise read nothing and fall back to the instance account.
- Workspace backups leave the table out, as they do `bank_connections`. A new instance key re-encrypts it with the other saved credentials ([0020](0020-instance-secrets-in-the-database.md)).

## Consequences

- Anything a provider caches from its credentials has to be keyed by account, not held once per process. Pluggy's API keys are.
- The connection picker reads the workspace's own values, so `GET /api/connections/providers` now needs a signed-in member of the workspace.
- Credentials belong to the workspace, not to the person: two people sharing one workspace share its account.
- Adding a provider takes an entry in `WORKSPACE_PROVIDERS`, a per-account cache where it has one, its texts under `providerSettings.workspaces.<provider>`, and an input of its own for a value that isn't one line (`docs/provider-settings.md`).

## Enforced by

- `backend/tests/test_workspace_provider_wiring.py`: every path above builds the provider with the workspace's account, and the picker follows the workspace.
- `backend/tests/test_provider_settings.py`:
  - the fallback;
  - no fallback from an unreadable value;
  - every refused account change, the allowed rotation and the recovery;
  - secrets never echoed;
  - the managed-instance lock;
  - the per-client key cache.
- `backend/tests/test_row_security_postgres.py`: the admin list and the account check under the restricted role.
- `backend/tests/test_provider_settings_postgres.py`: each change waits for the lock, two first saves don't collide, and a removal and an instance change can't both pass.
- `backend/tests/test_instance_secrets.py`: the re-encryption.
