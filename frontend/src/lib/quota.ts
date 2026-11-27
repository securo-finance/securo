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
  'max_assistant_messages_per_month',
] as const

/**
 * Counters whose number belongs to whoever created the workspace. The monthly
 * assistant messages are the owner's too (ADR 0053), but its one sentence
 * already reads true for a member, so it keeps a single key.
 */
const OWNER_COUNTERS = new Set(['max_bank_connections', 'max_members_per_workspace'])

export function readQuotaRefusal(error: unknown): QuotaUsage | null {
  return quotaFromDetail((error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail)
}

/** The same refusal read from a raw response body, as the chat stream's fetch gets it. */
export function readQuotaRefusalBody(body: string): QuotaUsage | null {
  try {
    return quotaFromDetail((JSON.parse(body) as { detail?: unknown } | null)?.detail)
  } catch {
    return null
  }
}

function quotaFromDetail(detail: unknown): QuotaUsage | null {
  if (!detail || typeof detail !== 'object') return null
  const { code, counter, current, maximum } = detail as Record<string, unknown>
  if (code !== 'quota_exceeded' || typeof counter !== 'string') return null
  return { counter, current: Number(current) || 0, maximum: Number(maximum) || 0 }
}

/**
 * The translation key for a filled counter. `ownedByOther`: the workspace was
 * created by someone else, so its connections and seats fill their number.
 * A `maximum` of 0 gets a sentence of its own: removing something can't free a
 * place, so the usual advice would be wrong. That sentence takes `label`, the
 * counter's name (`quotaMessageValues` fills it).
 */
export function quotaMessageKey(counter: string, ownedByOther: boolean, maximum?: number): string {
  if (!(QUOTA_COUNTERS as readonly string[]).includes(counter)) return 'quota.reachedGeneric'
  const owners = ownedByOther && OWNER_COUNTERS.has(counter)
  if (maximum === 0) return owners ? 'quota.ownerNone' : 'quota.none'
  return owners ? `quota.ownerReached.${counter}` : `quota.reached.${counter}`
}

/** The values every quota sentence reads, the counter's translated name included. */
export function quotaMessageValues(
  t: (key: string) => string,
  usage: QuotaUsage,
): { current: number; maximum: number; label: string } {
  return { current: usage.current, maximum: usage.maximum, label: t(`quota.counters.${usage.counter}`) }
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
