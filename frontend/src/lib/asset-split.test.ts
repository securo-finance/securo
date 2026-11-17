import { describe, expect, it } from 'vitest'

import {
  MAX_SPLIT_UNITS,
  canRecordSplit,
  formatSplitRatio,
  parseSplitUnits,
} from '@/lib/asset-split'

describe('formatSplitRatio', () => {
  it('reads old → new, so a 100-for-1 split is 1 → 100', () => {
    expect(formatSplitRatio(1, 100, 'en')).toBe('1 → 100')
  })

  it('shows a reverse split the other way round', () => {
    expect(formatSplitRatio(10, 1, 'en')).toBe('10 → 1')
  })

  it('groups thousands the way the locale does', () => {
    expect(formatSplitRatio(1, 1000, 'en')).toBe('1 → 1,000')
    expect(formatSplitRatio(1, 1000, 'de-DE')).toBe('1 → 1.000')
  })
})

describe('parseSplitUnits', () => {
  it('reads the two inputs as a ratio', () => {
    expect(parseSplitUnits('1', '100')).toEqual({ oldUnits: 1, newUnits: 100 })
    expect(parseSplitUnits('10', '1')).toEqual({ oldUnits: 10, newUnits: 1 })
  })

  it('accepts both ends of the allowed range', () => {
    expect(parseSplitUnits('1', String(MAX_SPLIT_UNITS))).toEqual({
      oldUnits: 1,
      newUnits: MAX_SPLIT_UNITS,
    })
    expect(parseSplitUnits(String(MAX_SPLIT_UNITS), '1')).toEqual({
      oldUnits: MAX_SPLIT_UNITS,
      newUnits: 1,
    })
  })

  it.each([
    ['both empty', '', ''],
    ['no old units', '', '100'],
    ['no new units', '1', ''],
    ['zero old units', '0', '100'],
    ['zero new units', '1', '0'],
    ['negative old units', '-1', '100'],
    ['negative new units', '1', '-100'],
    ['decimal old units', '1.5', '3'],
    ['decimal new units', '1', '2.5'],
    ['not a number', 'abc', '2'],
    ['old units above the cap', String(MAX_SPLIT_UNITS + 1), '1'],
    ['new units above the cap', '1', String(MAX_SPLIT_UNITS + 1)],
    ['the same number on both sides', '5', '5'],
  ])('rejects %s', (_label, oldInput, newInput) => {
    expect(parseSplitUnits(oldInput, newInput)).toBeNull()
  })
})

describe('canRecordSplit', () => {
  const eligible = { valuation_method: 'market_price', connection_id: null, transaction_count: 3 }

  it('allows a market-priced holding tracked here with buys on record', () => {
    expect(canRecordSplit(eligible)).toBe(true)
  })

  it('refuses a holding that is not market-priced', () => {
    expect(canRecordSplit({ ...eligible, valuation_method: 'manual' })).toBe(false)
  })

  it('refuses a holding synced from a bank connection', () => {
    expect(canRecordSplit({ ...eligible, connection_id: 'conn-1' })).toBe(false)
  })

  it('refuses a holding with no transactions yet', () => {
    expect(canRecordSplit({ ...eligible, transaction_count: 0 })).toBe(false)
  })
})
