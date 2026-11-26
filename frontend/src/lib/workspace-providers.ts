/**
 * Providers whose credentials a workspace can replace with its own, with the
 * fields the admin fills in. Each needs the same entry in the backend's
 * `WORKSPACE_PROVIDERS` (`app/services/provider_settings.py`) and its texts
 * under `providerSettings.workspaces.<provider>` in every locale.
 */
export const WORKSPACE_PROVIDERS: Record<string, { fields: string[] }> = {
  pluggy: { fields: ['pluggy_client_id', 'pluggy_client_secret'] },
}
