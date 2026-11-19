import { auth } from '@/lib/api'

/**
 * End the OIDC provider's own session after the local sign-out (issue #1623).
 *
 * The token Securo drops is not the provider's SSO cookie: without this, the
 * next sign-in goes straight back to the same account. The backend answers
 * where to send the browser (`logout_url`, from OIDC_LOGOUT_URL or the
 * discovery document's `end_session_endpoint`); an empty answer, or a
 * provider that is down, leaves the user signed out here — ending the
 * provider session is best effort and never blocks the local sign-out.
 */
export async function endProviderSession(
  redirect: (url: string) => void = (url) => window.location.assign(url),
): Promise<void> {
  try {
    const config = await auth.oidcConfig()
    if (config.logout_url) redirect(config.logout_url)
  } catch {
    // The local sign-out already happened; nothing else to do.
  }
}
