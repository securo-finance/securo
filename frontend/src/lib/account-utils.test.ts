import { describe, expect, it } from 'vitest'
import { sidebarAccountBalance, sortAccountsByAbsoluteBalance, sortAccountsByDisplayName, sumAccountsTotal } from './account-utils'

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

describe('sumAccountsTotal', () => {
  it('leaves loans out of the accounts total', () => {
    const accounts = [
      { type: 'checking', current_balance: 4000, balance_primary: null, shared_balance_group: null },
      { type: 'credit_card', current_balance: -500, balance_primary: null, shared_balance_group: null },
      { type: 'loan', current_balance: -200000, balance_primary: null, shared_balance_group: null },
    ]

    expect(sumAccountsTotal(accounts)).toBe(3500)
  })
})

describe('sidebarAccountBalance', () => {
  const sharedCard = (extra: Record<string, unknown> = {}) => ({
    type: 'credit_card',
    shared_balance_group: 'provider:line-1',
    balance_primary: null,
    current_balance: -1500,
    open_bill_total: null,
    open_bill_total_primary: null,
    ...extra,
  })

  it('shows a shared card own open bill instead of the line balance', () => {
    const { amount, isPrimary } = sidebarAccountBalance(
      sharedCard({ open_bill_total: 820.5 }),
    )

    expect(amount).toBe(-820.5)
    expect(isPrimary).toBe(false)
  })

  it('prefers the converted bill when the account is in a foreign currency', () => {
    const { amount, isPrimary } = sidebarAccountBalance(
      sharedCard({ open_bill_total: 200, open_bill_total_primary: 1000 }),
    )

    expect(amount).toBe(-1000)
    expect(isPrimary).toBe(true)
  })

  it('keeps the balance when a shared card has no open bill', () => {
    const { amount, isPrimary } = sidebarAccountBalance(sharedCard())

    expect(amount).toBe(-1500)
    expect(isPrimary).toBe(false)
  })

  it('keeps the balance for a non-shared card even if a bill value is present', () => {
    const { amount, isPrimary } = sidebarAccountBalance(
      sharedCard({ shared_balance_group: null, open_bill_total: 700 }),
    )

    expect(amount).toBe(-1500)
    expect(isPrimary).toBe(false)
  })

  it('keeps the converted balance for a non-credit-card account', () => {
    const { amount, isPrimary } = sidebarAccountBalance(
      sharedCard({ type: 'checking', balance_primary: 90, open_bill_total: null }),
    )

    expect(amount).toBe(90)
    expect(isPrimary).toBe(true)
  })
})
