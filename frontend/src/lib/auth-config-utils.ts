export type LocalAuthConfig = {
  local_auth_enabled: boolean
}

/**
 * Hide local credential controls while configuration is unresolved, but
 * preserve access when the optional OIDC-config request itself fails. The
 * backend remains authoritative and rejects local auth in OIDC-only mode.
 */
export function resolveLocalAuthEnabled(
  config: LocalAuthConfig | null | undefined,
  configFailed: boolean,
): boolean {
  return config?.local_auth_enabled ?? configFailed
}
