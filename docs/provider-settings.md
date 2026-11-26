# Provider settings

Administrators can configure Open Exchange Rates, Pluggy, and Enable Banking in **Admin Settings**: Open Exchange Rates under **Exchange rates**, the bank providers under **Bank connections**. SimpleFIN has an instance enable switch; its setup tokens and access URLs remain specific to each bank connection.

Saved values override environment variables field by field. Leaving a field blank in the form keeps its current value. Removing a saved value restores the environment fallback. Saved credentials are encrypted in `app_settings` using the existing Fernet key derived from `SECRET_KEY`; the API returns only configuration status and source, never a value or ciphertext. Set a unique `SECRET_KEY` of at least 32 characters before saving credentials, and keep it stable. After rotating it, re-enter saved provider credentials.

An in-app Enable Banking private key overrides `ENABLE_BANKING_PRIVATE_KEY_FILE`. If a saved credential can no longer be decrypted, the status reports it as invalid and the provider stays unavailable until an administrator replaces or removes it. An invalid saved value never silently falls back to a different environment credential.

A workspace can use its own Pluggy client: set it under **Admin Settings → Bank connections → Pluggy clients by workspace**. Workspaces without one use the instance credentials above, so set a workspace's client before anyone connects a bank there. A Pluggy connection only exists under the client that created it, so changing a workspace to a different client ID, or removing its client, is refused while the workspace has Pluggy connections; delete those first. Replacing the secret for the same client ID is always allowed. A saved workspace client that can no longer be decrypted makes Pluggy unavailable in that workspace; it never falls back to the instance client. The connection picker shows each workspace what its own credentials allow. On a managed instance ([ADR 0045](adr/0045-managed-instance.md)) workspace clients are read-only, like the instance credentials. They live in `workspace_provider_credentials` (migration `136`), are not part of workspace backups, and rolling back past `136` drops them. See [ADR 0049](adr/0049-workspace-provider-credentials.md).

Workspaces without their own client use the instance one, so changing the instance client ID, or removing it so a different environment client applies, is refused too while any of those workspaces has Pluggy connections. Give those workspaces their own client first, or delete their connections.

Only Pluggy takes credentials per workspace today. To add another provider whose connections live under one account of the instance (Enable Banking's application, for example):

1. Add it to `WORKSPACE_PROVIDERS` in `backend/app/services/provider_settings.py`, with its fields from `PROVIDER_FIELDS` and the field that names the account its connections are created under. The routes under `/api/admin/provider-settings/{provider}/workspaces` then accept it, and changing a workspace with connections to another account is refused the same way.
2. Cache anything derived from credentials per account, not once per process: see the Pluggy API keys in `backend/app/providers/pluggy.py`.
3. Add it to `WORKSPACE_PROVIDERS` in `frontend/src/lib/workspace-providers.ts`, and its texts under `providerSettings.workspaces.<provider>` in every locale. A field that needs more than a one-line input, such as a private key, needs its own input in `frontend/src/components/workspace-provider-credentials.tsx`.

Provider availability and credentials are read for each new operation, including worker jobs. An operation already running may finish with its earlier credentials. Configuration status confirms that required fields are present; it does not verify them with the provider.

Migration `113` changes `app_settings.value` to `TEXT` so an encrypted PEM key fits. The migration does not copy environment credentials into the database.

Rolling back past `113` shrinks `app_settings.value` to 2000 characters again, and the downgrade fails while a longer value is stored; an encrypted Enable Banking private key usually is. Older versions also read providers only from environment variables and return any `app_settings` row to administrators, so saved credentials should not stay behind. Before rolling back:

1. Set the environment variables for every provider configured in the app (for the private key, `ENABLE_BANKING_PRIVATE_KEY_FILE`).
2. Remove each saved value in **Admin Settings**. The status then shows the environment as the source.
3. Check that nothing is left, by key only, so no credential is printed:

   ```sql
   SELECT key FROM app_settings
   WHERE key IN ('openexchangerates_app_id', 'pluggy_client_id', 'pluggy_client_secret',
                 'enable_banking_app_id', 'enable_banking_private_key', 'simplefin_enabled');
   ```

4. Run `alembic downgrade 112`, then deploy the older version.
