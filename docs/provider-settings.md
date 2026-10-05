# Provider settings

Administrators can configure Open Exchange Rates, Pluggy, and Enable Banking in **Admin Settings → Providers**. SimpleFIN has an instance enable switch; its setup tokens and access URLs remain specific to each bank connection.

Saved values override environment variables field by field. Leaving a field blank in the form keeps its current value. Removing a saved value restores the environment fallback. Saved credentials are encrypted in `app_settings` using the existing Fernet key derived from `SECRET_KEY`; the API returns only configuration status and source, never a value or ciphertext. Set a unique `SECRET_KEY` of at least 32 characters before saving credentials, and keep it stable. After rotating it, re-enter saved provider credentials.

An in-app Enable Banking private key overrides `ENABLE_BANKING_PRIVATE_KEY_FILE`. If a saved credential can no longer be decrypted, the status reports it as invalid and the provider stays unavailable until an administrator replaces or removes it. An invalid saved value never silently falls back to a different environment credential.

Provider availability and credentials are read for each new operation, including worker jobs. An operation already running may finish with its earlier credentials. Configuration status confirms that required fields are present; it does not verify them with the provider.

Migration `097` changes `app_settings.value` to `TEXT` so an encrypted PEM key fits. The migration does not copy environment credentials into the database.
