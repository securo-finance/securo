import { describe, expect, it } from 'vitest'

import { amountToCents, parseCents } from '@/lib/mortgage-split'

describe('mortgage split cents', () => {
  it('parses whole and two-decimal amounts into cents', () => {
    expect(parseCents('167.76')).toBe(16776)
    expect(parseCents('65,07')).toBe(6507)
    expect(parseCents('150')).toBe(15000)
    expect(parseCents('0.5')).toBe(50)
    expect(parseCents('')).toBe(0)
  })

  it('rejects fractional cents and malformed input', () => {
    expect(parseCents('100.001')).toBeNull()
    expect(parseCents('-5')).toBeNull()
    expect(parseCents('abc')).toBeNull()
  })

  it('catches a one-cent mismatch that float tolerance let through', () => {
    const total = (parseCents('100.00') ?? 0) + (parseCents('132.84') ?? 0)
    expect(total).not.toBe(amountToCents(232.83))
    expect((parseCents('100.00') ?? 0) + (parseCents('132.83') ?? 0)).toBe(amountToCents(232.83))
  })
})
