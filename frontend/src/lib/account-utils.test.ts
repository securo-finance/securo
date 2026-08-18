import { describe, expect, it } from 'vitest'
import { sortAccountsByDisplayName } from './account-utils'

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
