import type { MyQuotas, QuotaUsage } from '@/types'

/**
 * Usage limits on a managed instance (ADR 0049, #1972).
 *
 * The API refuses with `403` and `detail.code = "quota_exceeded"`, naming the
 * counter that filled. These helpers read that refusal and pick the sentence
 * that says which limit filled and what frees a place.
 */

export const QUOTA_COUNTERS = [
  'max_personal_workspaces',
  'max_business_workspaces',
  'max_bank_connections',
  'max_members_per_workspace',
] as const

/** Counters whose number belongs to whoever created the workspace. */
const OWNER_COUNTERS = new Set(['max_bank_connections', 'max_members_per_workspace'])

export function readQuotaRefusal(error: unknown): QuotaUsage | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  if (!detail || typeof detail !== 'object') return null
  const { code, counter, current, maximum } = detail as Record<string, unknown>
  if (code !== 'quota_exceeded' || typeof counter !== 'string') return null
  return { counter, current: Number(current) || 0, maximum: Number(maximum) || 0 }
}

/**
 * The translation key for a filled counter. `ownedByOther`: the workspace was
 * created by someone else, so its connections and seats fill their number.
 */
export function quotaMessageKey(counter: string, ownedByOther: boolean): string {
  if (!(QUOTA_COUNTERS as readonly string[]).includes(counter)) return 'quota.reachedGeneric'
  return ownedByOther && OWNER_COUNTERS.has(counter)
    ? `quota.ownerReached.${counter}`
    : `quota.reached.${counter}`
}

/**
 * The operator's page about the limits, kept only when it is a web address or
 * a path on this site, so a mistyped setting never becomes a script link.
 */
export function safeInfoUrl(url: string | null | undefined): string | null {
  const value = url?.trim()
  if (!value) return null
  return /^https?:\/\//i.test(value) || (value.startsWith('/') && !value.startsWith('//')) ? value : null
}

/** The counter when it is already at its maximum, so the action can stop before the attempt. */
export function fullCounter(quotas: MyQuotas | undefined, counter: string): QuotaUsage | null {
  const usage = quotas?.counters.find((c) => c.counter === counter)
  return usage && usage.current >= usage.maximum ? usage : null
}
