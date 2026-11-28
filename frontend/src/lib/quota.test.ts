import { describe, expect, it } from 'vitest'
import { fullCounter, quotaMessageKey, readQuotaRefusal, safeInfoUrl } from './quota'
import type { MyQuotas } from '@/types'

const refusal = (detail: unknown) => ({ isAxiosError: true, response: { status: 403, data: { detail } } })

describe('readQuotaRefusal', () => {
  it('reads the counter, count and maximum of a quota_exceeded', () => {
    const error = refusal({ code: 'quota_exceeded', counter: 'max_bank_connections', current: 2, maximum: 2 })
    expect(readQuotaRefusal(error)).toEqual({ counter: 'max_bank_connections', current: 2, maximum: 2 })
  })

  it('ignores any other refusal', () => {
    expect(readQuotaRefusal(refusal({ code: 'instance_managed', message: 'x' }))).toBeNull()
    expect(readQuotaRefusal(refusal('Not enough permissions'))).toBeNull()
    expect(readQuotaRefusal(new Error('network'))).toBeNull()
    expect(readQuotaRefusal(undefined)).toBeNull()
  })
})

describe('quotaMessageKey', () => {
  it('names the counter for the account that holds the limit', () => {
    expect(quotaMessageKey('max_personal_workspaces', false)).toBe('quota.reached.max_personal_workspaces')
  })

  it('says the limit is the owner’s in a workspace someone else created', () => {
    expect(quotaMessageKey('max_bank_connections', true)).toBe('quota.ownerReached.max_bank_connections')
    expect(quotaMessageKey('max_members_per_workspace', true)).toBe('quota.ownerReached.max_members_per_workspace')
  })

  it('keeps workspace counters on the account itself, which is always the creator', () => {
    expect(quotaMessageKey('max_business_workspaces', true)).toBe('quota.reached.max_business_workspaces')
  })

  it('never suggests removing something when the limit is 0, since nothing frees a place', () => {
    expect(quotaMessageKey('max_bank_connections', false, 0)).toBe('quota.none')
    expect(quotaMessageKey('max_personal_workspaces', false, 0)).toBe('quota.none')
    expect(quotaMessageKey('max_members_per_workspace', true, 0)).toBe('quota.ownerNone')
    expect(quotaMessageKey('max_business_workspaces', true, 0)).toBe('quota.none')
    expect(quotaMessageKey('max_bank_connections', false, 2)).toBe('quota.reached.max_bank_connections')
  })

  it('falls back to a generic sentence for a counter it does not know', () => {
    expect(quotaMessageKey('max_something_new', false)).toBe('quota.reachedGeneric')
  })
})

describe('safeInfoUrl', () => {
  it('keeps a web address or a path on this site', () => {
    expect(safeInfoUrl(' /help/limits ')).toBe('/help/limits')
    expect(safeInfoUrl('https://help.invalid/limits')).toBe('https://help.invalid/limits')
  })

  it('drops an empty setting and anything that is not a link', () => {
    expect(safeInfoUrl(null)).toBeNull()
    expect(safeInfoUrl('  ')).toBeNull()
    expect(safeInfoUrl('javascript:alert(1)')).toBeNull()
    expect(safeInfoUrl('//elsewhere.invalid')).toBeNull()
  })
})

describe('fullCounter', () => {
  const quotas: MyQuotas = {
    enforced: true,
    info_url: null,
    counters: [
      { counter: 'max_personal_workspaces', current: 1, maximum: 1 },
      { counter: 'max_bank_connections', current: 1, maximum: 3 },
    ],
  }

  it('returns the counter when it is at its maximum', () => {
    expect(fullCounter(quotas, 'max_personal_workspaces')).toEqual(quotas.counters[0])
  })

  it('is null below the maximum, for an unbounded counter and without data', () => {
    expect(fullCounter(quotas, 'max_bank_connections')).toBeNull()
    expect(fullCounter(quotas, 'max_business_workspaces')).toBeNull()
    expect(fullCounter(undefined, 'max_personal_workspaces')).toBeNull()
  })
})
