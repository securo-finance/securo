import type { SyncFailureCode } from '@/types'

/** What the user can do about a failed sync. */
export type SyncFailureAction = 'sync' | 'reconnect' | 'wait'

/** Translation key of the message for each failure code. */
export const SYNC_FAILURE_MESSAGE_KEY: Record<SyncFailureCode, string> = {
  psu_present_required: 'accounts.syncFailure.psu_present_required',
  consent_expired: 'accounts.syncFailure.consent_expired',
  reauthorize_at_provider: 'accounts.syncFailure.reauthorize_at_provider',
  rate_limited: 'accounts.syncRateLimited',
  provider_error: 'accounts.syncFailure.provider_error',
}

const ACTIONS: Record<SyncFailureCode, SyncFailureAction> = {
  // The bank serves this data only while the user is there: a manual sync
  // sends their presence.
  psu_present_required: 'sync',
  consent_expired: 'reconnect',
  // Fixed at the SimpleFIN Bridge, not here: once done, a sync picks it up.
  reauthorize_at_provider: 'sync',
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
 * it is not a bank failure: the message is for the operator (a provider the
 * server is not configured for) or the connection was disconnected.
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
    // A 409 without a code is the connection disconnected meanwhile, not a
    // bank refusal: the caller says so itself.
    if (typeof code !== 'string') return null
    if (code === 'psu_present_required') return 'psu_present_required'
    if (RECONNECT_CODES.has(code)) return 'consent_expired'
    return 'provider_error'
  }
  return 'provider_error'
}
