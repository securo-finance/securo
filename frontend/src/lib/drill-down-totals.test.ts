import { describe, expect, it } from 'vitest'

import { sumDrillDownTotals } from '@/lib/drill-down-totals'
import type { DrillDownTotalsItem } from '@/lib/drill-down-totals'

const row = (overrides: Partial<DrillDownTotalsItem> = {}): DrillDownTotalsItem => ({
  amount: 100,
  amountPrimary: null,
  currency: 'USD',
  isPending: false,
  isProjected: false,
  ...overrides,
})

describe('sumDrillDownTotals', () => {
  it('splits rows into posted, pending and projected', () => {
    const totals = sumDrillDownTotals([
      row({ amount: 200 }),
      row({ amount: 100, isPending: true }),
      row({ amount: 487.68, isProjected: true }),
    ], 'USD')

    expect(totals.postedTotal).toBeCloseTo(200)
    expect(totals.pendingTotal).toBeCloseTo(100)
    expect(totals.projectedTotal).toBeCloseTo(487.68)
    expect(totals.absTotal).toBeCloseTo(787.68)
  })

  it('keeps a projected row inside the total when the panel also has a pending one', () => {
    // The regression: a projected row is neither posted nor pending, so
    // bucketing it by transaction status dropped it from the footer while
    // the list above still showed it.
    const totals = sumDrillDownTotals([
      row({ amount: 100, isPending: true }),
      row({ amount: 487.68, isProjected: true }),
    ], 'USD')

    expect(totals.postedTotal + totals.pendingTotal + totals.projectedTotal)
      .toBeCloseTo(totals.absTotal)
    expect(totals.absTotal).toBeCloseTo(587.68)
  })

  it('never loses a row: the three buckets always rebuild the total', () => {
    const totals = sumDrillDownTotals([
      row({ amount: 10 }),
      row({ amount: 20, isPending: true }),
      row({ amount: 30, isProjected: true }),
      row({ amount: 40, isPending: true, isProjected: true }),
    ], 'USD')

    expect(totals.postedTotal + totals.pendingTotal + totals.projectedTotal)
      .toBeCloseTo(totals.absTotal)
  })

  it('counts a foreign row through its primary amount', () => {
    expect(sumDrillDownTotals([
      row({ amount: 2500, amountPrimary: 487.68, currency: 'BRL' }),
    ], 'USD')).toMatchObject({ absTotal: 487.68, postedTotal: 487.68 })
  })

  it('counts the primary stamp of a row already in the user currency', () => {
    // Issue #467: the row's own amount is the account charge (10.03); the
    // stamp is the conversion the user typed (10.00, ADR 0031), which is the
    // figure the dashboard counter and the list summary total. The footer
    // follows the stamp so it agrees with the counter that opened it.
    expect(sumDrillDownTotals([
      row({ amount: 10.03, amountPrimary: 10, currency: 'BRL' }),
    ], 'BRL')).toMatchObject({ absTotal: 10, postedTotal: 10 })
  })

  it('falls back to the amount when a row in the user currency has no stamp', () => {
    expect(sumDrillDownTotals([
      row({ amount: 42.5, amountPrimary: null, currency: 'BRL' }),
    ], 'BRL')).toMatchObject({ absTotal: 42.5 })
  })

  it('skips a foreign row with no conversion rather than counting it raw', () => {
    expect(sumDrillDownTotals([
      row({ amount: 2500, amountPrimary: null, currency: 'BRL' }),
    ], 'USD')).toEqual({
      absTotal: 0,
      netTotal: 0,
      postedTotal: 0,
      pendingTotal: 0,
      projectedTotal: 0,
    })
  })

  it('reads amounts as magnitudes, whatever sign they arrive with', () => {
    expect(sumDrillDownTotals([
      row({ amount: -100 }),
      row({ amount: 100 }),
    ], 'USD')).toMatchObject({ absTotal: 200, postedTotal: 200 })
  })

  it('nets credits against debits', () => {
    expect(sumDrillDownTotals([
      row({ amount: 513.48, type: 'debit' }),
      row({ amount: 120, type: 'credit' }),
    ], 'USD')).toMatchObject({ absTotal: 633.48, netTotal: -393.48 })
  })

  it('returns zeroes for an empty panel', () => {
    expect(sumDrillDownTotals([], 'USD')).toEqual({
      absTotal: 0,
      netTotal: 0,
      postedTotal: 0,
      pendingTotal: 0,
      projectedTotal: 0,
    })
  })
})
