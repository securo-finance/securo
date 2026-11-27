import { describe, expect, it } from 'vitest'

import { pocketAvailable, roundToCents, sumPocketAmounts, withdrawalCap } from './pocket-allocation-utils'

describe('pocket amounts in whole cents', () => {
  it('sends an available balance the API accepts', () => {
    // 100.1 - 0.2 is 99.89999999999999 in floats; the API takes two decimals.
    expect(pocketAvailable(100.1, 0.2)).toBe(99.9)
    expect(pocketAvailable(1000.1, 333.2)).toBe(666.9)
  })

  it('shows a fully reserved account as zero, never below it', () => {
    const reserved = sumPocketAmounts([0.1, 0.2])
    expect(reserved).toBe(0.3)
    const available = pocketAvailable(0.3, reserved)
    expect(available).toBe(0)
    expect(Object.is(available, -0)).toBe(false)
  })

  it('rounds a typed amount to cents', () => {
    expect(roundToCents(12.345)).toBe(12.35)
    expect(roundToCents(0)).toBe(0)
  })
})

describe('withdrawalCap', () => {
  it('adds back what the withdrawal already takes from the pocket', () => {
    expect(withdrawalCap(0, -100, 100)).toBe(100)
    expect(withdrawalCap(40, -60, 100)).toBe(100)
  })

  it('never exceeds the transaction amount', () => {
    expect(withdrawalCap(500, -60, 100)).toBe(100)
  })

  it('does not count a saved deposit as money the withdrawal can take', () => {
    expect(withdrawalCap(40, 50, 100)).toBe(0)
    expect(withdrawalCap(40, 0, 100)).toBe(40)
  })
})
