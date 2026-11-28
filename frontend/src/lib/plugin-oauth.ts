/**
 * Browser-side handoff for plugin redirect sign-in flows whose external service
 * does not echo Securo's single-use `state` parameter back on the completion
 * redirect (for example Plaid Hosted Link, ADR 0058).
 */
const PLUGIN_OAUTH_STATE_KEY = 'securo:pluginOAuthState'

export function rememberPluginOAuthState(state: string | null | undefined): void {
  try {
    if (state) sessionStorage.setItem(PLUGIN_OAUTH_STATE_KEY, state)
    else sessionStorage.removeItem(PLUGIN_OAUTH_STATE_KEY)
  } catch {
    // Storage may be unavailable in restricted contexts; URL state still works.
  }
}

export function peekPluginOAuthState(): string | null {
  try {
    return sessionStorage.getItem(PLUGIN_OAUTH_STATE_KEY)
  } catch {
    return null
  }
}

export function clearPluginOAuthState(): void {
  try {
    sessionStorage.removeItem(PLUGIN_OAUTH_STATE_KEY)
  } catch {
    // Ignore unavailable storage.
  }
}
