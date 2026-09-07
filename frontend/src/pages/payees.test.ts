import { describe, expect, it } from 'vitest'
import type { Payee } from '@/types'
import { DEFAULT_PAYEE_SORT, loadPayeeSort, PAYEE_SORT_STORAGE_KEY, sortPayees } from '@/lib/payee-sorting'

const typeLabels = { person: 'Person', company: 'Company' }

function payee(name: string, overrides: Partial<Payee> = {}): Payee {
  return {
    id: name,
    user_id: 'user-1',
    name,
    type: null,
    source: 'manual',
    is_favorite: false,
    notes: null,
    email: null,
    phone: null,
    address: null,
    website: null,
    tax_ids: [],
    created_at: '2026-01-01T00:00:00Z',
    transaction_count: 0,
    ...overrides,
  }
}

describe('sortPayees', () => {
  it('sorts names ascending without changing the source list', () => {
    const payees = [payee('Zeta'), payee('Alpha')]

    const result = sortPayees(payees, { by: 'name', direction: 'asc' }, 'en-US', typeLabels)

    expect(result.map(({ name }) => name)).toEqual(['Alpha', 'Zeta'])
    expect(payees.map(({ name }) => name)).toEqual(['Zeta', 'Alpha'])
  })

  it('sorts transaction counts descending', () => {
    const payees = [payee('One', { transaction_count: 1 }), payee('Three', { transaction_count: 3 })]

    const result = sortPayees(payees, { by: 'transaction_count', direction: 'desc' }, 'en-US', typeLabels)

    expect(result.map(({ name }) => name)).toEqual(['Three', 'One'])
  })

  it('moves unset types with the selected direction', () => {
    const payees = [payee('Unset'), payee('Person', { type: 'person' })]

    const ascending = sortPayees(payees, { by: 'type', direction: 'asc' }, 'en-US', typeLabels)
    const descending = sortPayees(payees, { by: 'type', direction: 'desc' }, 'en-US', typeLabels)

    expect(ascending.map(({ name }) => name)).toEqual(['Person', 'Unset'])
    expect(descending.map(({ name }) => name)).toEqual(['Unset', 'Person'])
  })

  it('preserves API order for equal values', () => {
    const payees = [payee('First', { transaction_count: 2 }), payee('Second', { transaction_count: 2 })]

    const result = sortPayees(payees, { by: 'transaction_count', direction: 'asc' }, 'en-US', typeLabels)

    expect(result.map(({ name }) => name)).toEqual(['First', 'Second'])
  })
})

describe('loadPayeeSort', () => {
  it('uses the default when no saved preference exists', () => {
    expect(loadPayeeSort()).toEqual(DEFAULT_PAYEE_SORT)
  })

  it('loads a valid saved preference and rejects invalid data', () => {
    localStorage.setItem(PAYEE_SORT_STORAGE_KEY, JSON.stringify({ by: 'transaction_count', direction: 'desc' }))
    expect(loadPayeeSort()).toEqual({ by: 'transaction_count', direction: 'desc' })

    localStorage.setItem(PAYEE_SORT_STORAGE_KEY, JSON.stringify({ by: 'favorite', direction: 'asc' }))
    expect(loadPayeeSort()).toEqual(DEFAULT_PAYEE_SORT)
  })
})
