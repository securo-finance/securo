# Provider settings

Administrators can configure Open Exchange Rates, Pluggy, and Enable Banking in **Admin Settings → Providers**. SimpleFIN has an instance enable switch; its setup tokens and access URLs remain specific to each bank connection.

Saved values override environment variables field by field. Leaving a field blank in the form keeps its current value. Removing a saved value restores the environment fallback. Saved credentials are encrypted in `app_settings` using the existing Fernet key derived from `SECRET_KEY`; the API returns only configuration status and source, never a value or ciphertext. Set a unique `SECRET_KEY` of at least 32 characters before saving credentials, and keep it stable. After rotating it, re-enter saved provider credentials.

An in-app Enable Banking private key overrides `ENABLE_BANKING_PRIVATE_KEY_FILE`. If a saved credential can no longer be decrypted, the status reports it as invalid and the provider stays unavailable until an administrator replaces or removes it. An invalid saved value never silently falls back to a different environment credential.

Provider availability and credentials are read for each new operation, including worker jobs. An operation already running may finish with its earlier credentials. Configuration status confirms that required fields are present; it does not verify them with the provider.

Migration `102` changes `app_settings.value` to `TEXT` so an encrypted PEM key fits. The migration does not copy environment credentials into the database.

Rolling back past `102` shrinks `app_settings.value` to 2000 characters again, and the downgrade fails while a longer value is stored; an encrypted Enable Banking private key usually is. Older versions also read providers only from environment variables and return any `app_settings` row to administrators, so saved credentials should not stay behind. Before rolling back:

1. Set the environment variables for every provider configured in the app (for the private key, `ENABLE_BANKING_PRIVATE_KEY_FILE`).
2. Remove each saved value in **Admin Settings → Providers**. The status then shows the environment as the source.
3. Check that nothing is left, by key only, so no credential is printed:

   ```sql
   SELECT key FROM app_settings
   WHERE key IN ('openexchangerates_app_id', 'pluggy_client_id', 'pluggy_client_secret',
                 'enable_banking_app_id', 'enable_banking_private_key', 'simplefin_enabled');
   ```

4. Run `alembic downgrade 101`, then deploy the older version.
