import { describe, expect, it } from 'vitest'
import { sortAccountsByAbsoluteBalance, sortAccountsByDisplayName } from './account-utils'

describe('sortAccountsByDisplayName', () => {
  it('orders accounts by display name when present and falls back to name', () => {
    const accounts = [
      { id: '1', name: 'Alpha', display_name: 'Vacation' },
      { id: '2', name: 'Zulu', display_name: null },
      { id: '3', name: 'Checking', display_name: 'Bills' },
    ]

    expect(sortAccountsByDisplayName(accounts).map((account) => account.id)).toEqual([
      '3',
      '1',
      '2',
    ])
  })

  it('does not mutate the API account list', () => {
    const accounts = [{ name: 'Zulu' }, { name: 'Alpha' }]

    sortAccountsByDisplayName(accounts)

    expect(accounts.map((account) => account.name)).toEqual(['Zulu', 'Alpha'])
  })
})

describe('sortAccountsByAbsoluteBalance', () => {
  it('orders multi-currency accounts by the magnitude of their converted balances', () => {
    const accounts = [
      { id: 'brl', current_balance: '1000', balance_primary: 185 },
      { id: 'eur', current_balance: '500', balance_primary: 580 },
      { id: 'debt', current_balance: '-100', balance_primary: -700 },
    ]

    const sorted = sortAccountsByAbsoluteBalance(accounts, (a) => a.balance_primary ?? a.current_balance)

    expect(sorted.map((a) => a.id)).toEqual(['debt', 'eur', 'brl'])
    expect(accounts.map((a) => a.id)).toEqual(['brl', 'eur', 'debt'])
  })

  it('preserves converted zero and falls back only when the converted balance is missing', () => {
    const accounts = [
      { id: 'converted-zero', current_balance: '1000', balance_primary: 0 },
      { id: 'null-conversion', current_balance: '200', balance_primary: null },
      { id: 'no-conversion', current_balance: '300' },
      { id: 'converted', current_balance: '500', balance_primary: 100 },
    ]

    expect(sortAccountsByAbsoluteBalance(accounts, (a) => a.balance_primary ?? a.current_balance)
      .map((a) => a.id)).toEqual(['no-conversion', 'null-conversion', 'converted', 'converted-zero'])
  })

  it('orders positive and negative balances by magnitude', () => {
    const accounts = [
      { id: 'small-positive', current_balance: '8.22' },
      { id: 'largest-positive', current_balance: '6936.72' },
      { id: 'large-negative', current_balance: '-1200.50' },
      { id: 'zero', current_balance: '0' },
    ]

    expect(sortAccountsByAbsoluteBalance(accounts).map((account) => account.id)).toEqual([
      'largest-positive',
      'large-negative',
      'small-positive',
      'zero',
    ])
  })

  it('does not mutate the API account list', () => {
    const accounts = [
      { id: 'small', current_balance: 10 },
      { id: 'large', current_balance: 20 },
    ]

    sortAccountsByAbsoluteBalance(accounts)

    expect(accounts.map((account) => account.id)).toEqual(['small', 'large'])
  })
})
