import { describe, expect, it } from 'vitest'

import {
  formatTransactionTotal,
  getTransactionTotalCurrency,
} from '@/lib/asset-transaction-total'

describe('asset transaction total', () => {
  const holdings = [
    { id: 'petr4', currency: 'BRL' },
    { id: 'aapl', currency: 'USD' },
  ]

  it('uses the selected holding currency, not a fixed one', () => {
    expect(getTransactionTotalCurrency(holdings, 'petr4', null)).toBe('BRL')
    expect(getTransactionTotalCurrency(holdings, 'aapl', null)).toBe('USD')
  })

  it('falls back to the edited transaction currency when its holding is not listed', () => {
    expect(getTransactionTotalCurrency(holdings, 'sold', { currency: 'EUR' })).toBe('EUR')
    expect(getTransactionTotalCurrency(holdings, 'petr4', { currency: 'EUR' })).toBe('BRL')
  })

  it('has no currency for a new ticker until the server prices it', () => {
    expect(getTransactionTotalCurrency(holdings, '__new__', null)).toBeNull()
    expect(getTransactionTotalCurrency(holdings, 'sold', { currency: null })).toBeNull()
  })

  it('formats the total in the given currency', () => {
    expect(formatTransactionTotal(100, 'BRL', 'pt-BR')).toBe(
      new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(100),
    )
    expect(formatTransactionTotal(100, 'BRL', 'pt-BR')).not.toContain('US$')
  })

  it('formats a bare amount when the currency is unknown', () => {
    expect(formatTransactionTotal(1234.5, null, 'en-US')).toBe('1,234.50')
    expect(formatTransactionTotal(1234.5, null, 'pt-BR')).toBe('1.234,50')
  })
})
