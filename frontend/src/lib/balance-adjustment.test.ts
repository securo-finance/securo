import { describe, expect, it } from 'vitest'

import { getBalanceAdjustmentPreview } from './balance-adjustment'

describe('getBalanceAdjustmentPreview', () => {
  it('calculates a signed delta for regular accounts', () => {
    expect(getBalanceAdjustmentPreview('checking', 300, 230)).toEqual({
      currentInputValue: 300,
      targetSignedBalance: 230,
      adjustmentAmount: -70,
    })
  })

  it('accepts a positive amount owed for credit cards', () => {
    expect(getBalanceAdjustmentPreview('credit_card', -500, 320)).toEqual({
      currentInputValue: 500,
      targetSignedBalance: -320,
      adjustmentAmount: 180,
    })
  })

  it('can remove a positive credit from a card with a zero owed target', () => {
    expect(getBalanceAdjustmentPreview('credit_card', 20, 0)).toEqual({
      currentInputValue: 0,
      targetSignedBalance: 0,
      adjustmentAmount: -20,
    })
  })
})
