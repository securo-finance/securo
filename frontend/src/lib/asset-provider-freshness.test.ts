import { describe, expect, it } from 'vitest'

import { getStaleProviderValueDate } from '@/lib/asset-provider-freshness'

const today = '2026-11-10'

describe('getStaleProviderValueDate', () => {
  it('flags an open position whose provider value is more than a week old', () => {
    expect(
      getStaleProviderValueDate({ provider_value_date: '2026-06-17', sell_date: null }, today),
    ).toBe('2026-06-17')
  })

  it('does not flag a value exactly a week old', () => {
    expect(
      getStaleProviderValueDate({ provider_value_date: '2026-11-03', sell_date: null }, today),
    ).toBeNull()
  })

  it('does not flag a recent value', () => {
    expect(
      getStaleProviderValueDate({ provider_value_date: '2026-11-09', sell_date: null }, today),
    ).toBeNull()
  })

  it('does not flag a sold position', () => {
    expect(
      getStaleProviderValueDate(
        { provider_value_date: '2026-06-17', sell_date: '2026-06-17' },
        today,
      ),
    ).toBeNull()
  })

  it('does not flag an asset without a provider value date', () => {
    expect(
      getStaleProviderValueDate({ provider_value_date: null, sell_date: null }, today),
    ).toBeNull()
  })

  it('does not flag a date in the future', () => {
    expect(
      getStaleProviderValueDate({ provider_value_date: '2026-11-20', sell_date: null }, today),
    ).toBeNull()
  })
})
