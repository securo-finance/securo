// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'

import { formatCurrency, formatSignedAmount, ltrAmount } from '@/lib/format'

const LRI = '⁦'
const PDI = '⁩'

describe('signed amounts in right-to-left pages', () => {
  afterEach(() => {
    document.documentElement.dir = ''
  })

  it('leaves amounts untouched on left-to-right pages', () => {
    expect(ltrAmount('-$5.00')).toBe('-$5.00')
    expect(formatCurrency(-5, 'USD', 'en-US')).toBe('-$5.00')
  })

  it('isolates amounts so the sign stays in front of the number', () => {
    document.documentElement.dir = 'rtl'
    expect(formatCurrency(-110, 'ILS', 'en-IL')).toMatch(new RegExp(`^${LRI}-.*110\\.00${PDI}$`))
    const signed = formatSignedAmount(5, 'credit', 'ILS', 'en-IL')
    expect(signed.startsWith(`${LRI}+`)).toBe(true)
    expect(signed.endsWith(PDI)).toBe(true)
  })
})
