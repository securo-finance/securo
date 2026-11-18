import type { SyncFailureCode } from '@/types'

/** What the user can do about a failed sync. */
export type SyncFailureAction = 'sync' | 'reconnect' | 'wait'

/** Translation key of the message for each failure code. */
export const SYNC_FAILURE_MESSAGE_KEY: Record<SyncFailureCode, string> = {
  psu_present_required: 'accounts.syncFailure.psu_present_required',
  consent_expired: 'accounts.syncFailure.consent_expired',
  rate_limited: 'accounts.syncRateLimited',
  provider_error: 'accounts.syncFailure.provider_error',
}

const ACTIONS: Record<SyncFailureCode, SyncFailureAction> = {
  // The bank serves this data only while the user is there: a manual sync
  // sends their presence.
  psu_present_required: 'sync',
  consent_expired: 'reconnect',
  // The bank's quota resets by itself; the scheduler retries.
  rate_limited: 'wait',
  provider_error: 'sync',
}

export function syncFailureAction(code: SyncFailureCode): SyncFailureAction {
  return ACTIONS[code]
}

// Provider user-action codes that only a new authorization fixes; the
// backend files them under consent_expired on the connection too.
const RECONNECT_CODES = new Set(['credentials_invalid', 'setup_token_used', 'no_accounts_linked'])

/**
 * The failure code of a manual sync that failed, from its HTTP status and
 * `detail`, matching what the backend stores on the connection. Null when
 * the message is for the operator (a provider the server is not configured
 * for), so the caller shows the server's own text.
 */
export function syncFailureCodeFromResponse(
  status: number | undefined,
  detail: unknown,
): SyncFailureCode | null {
  if (status === 503) return null
  if (status === 410) return 'consent_expired'
  if (status === 409) {
    const code =
      detail && typeof detail === 'object' && 'code' in detail
        ? (detail as { code?: unknown }).code
        : undefined
    if (code === 'psu_present_required') return 'psu_present_required'
    if (typeof code !== 'string' || RECONNECT_CODES.has(code)) return 'consent_expired'
    return 'provider_error'
  }
  return 'provider_error'
}
