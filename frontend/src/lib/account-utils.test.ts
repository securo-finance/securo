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
