import { describe, expect, it } from 'vitest'

import { getLoanDebtPrimary, getPropertyEquity } from '@/lib/property-equity'

describe('property equity', () => {
  const property = { id: 'home', current_value: 593000, current_value_primary: null }
  const loans = [
    { type: 'loan', secured_asset_id: 'home', current_balance: -194723.17, balance_primary: null },
    { type: 'loan', secured_asset_id: 'home', current_balance: -23556.84, balance_primary: null },
    { type: 'loan', secured_asset_id: 'other', current_balance: -48802.47, balance_primary: null },
  ]

  it('deducts every loan linked to the property once', () => {
    const result = getPropertyEquity(property, loans)
    expect(result?.debt).toBeCloseTo(218280.01, 2)
    expect(result?.equity).toBeCloseTo(374719.99, 2)
  })

  it('uses converted balances when a loan is in another currency', () => {
    const result = getPropertyEquity(
      { ...property, current_value_primary: 600000 },
      [{ ...loans[0], balance_primary: -200000 }],
    )
    expect(result).toEqual({ value: 600000, debt: 200000, equity: 400000 })
  })

  it('does not count an overpaid loan as debt', () => {
    expect(getLoanDebtPrimary({ current_balance: 25, balance_primary: null })).toBe(0)
  })

  it('has no equity to show for a property without a value', () => {
    expect(getPropertyEquity({ ...property, current_value: null }, loans)).toBeNull()
  })
})
