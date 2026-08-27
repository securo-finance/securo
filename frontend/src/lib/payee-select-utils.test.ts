import { describe, expect, it } from 'vitest'

import { filterPayees } from './payee-select-utils'
import type { Payee } from '../types'

function payee(name: string, overrides: Partial<Payee> = {}): Payee {
  return {
    id: name,
    user_id: 'u1',
    name,
    type: null,
    source: 'sync',
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

const names = (rows: Payee[]) => rows.map((p) => p.name)

describe('filterPayees', () => {
  it('matches without the accents the bank never types', () => {
    const rows = filterPayees([payee('São Paulo Energia'), payee('Uber')], 'sao')
    expect(names(rows)).toEqual(['São Paulo Energia'])
  })

  it('ignores case', () => {
    const rows = filterPayees([payee('UBER *TRIP'), payee('Padaria')], 'uber')
    expect(names(rows)).toEqual(['UBER *TRIP'])
  })

  it('matches in the middle of a name, not just at the start', () => {
    const rows = filterPayees([payee('Auto Posto Shell')], 'posto')
    expect(names(rows)).toEqual(['Auto Posto Shell'])
  })

  it('ranks a name that starts with the search above one that merely contains it', () => {
    const rows = filterPayees([payee('Auto Posto Shell'), payee('Posto Ipiranga')], 'posto')
    expect(names(rows)).toEqual(['Posto Ipiranga', 'Auto Posto Shell'])
  })

  it('breaks a tie in favour of a starred payee', () => {
    const rows = filterPayees(
      [payee('Posto Ipiranga'), payee('Posto Shell', { is_favorite: true })],
      'posto',
    )
    expect(names(rows)).toEqual(['Posto Shell', 'Posto Ipiranga'])
  })

  it('does not let a starred payee outrank a better match', () => {
    // Relevance first: a starred payee the user did not search for should not
    // sit above the one they typed the start of.
    const rows = filterPayees(
      [payee('Posto Ipiranga'), payee('Auto Posto Shell', { is_favorite: true })],
      'posto',
    )
    expect(names(rows)).toEqual(['Posto Ipiranga', 'Auto Posto Shell'])
  })

  it('leaves out what does not match at all', () => {
    const rows = filterPayees([payee('Uber'), payee('Padaria')], 'zzz')
    expect(rows).toEqual([])
  })

  it('shows starred payees first when nothing is typed', () => {
    const rows = filterPayees(
      [payee('Uber'), payee('Padaria', { is_favorite: true })],
      '',
    )
    expect(names(rows)).toEqual(['Padaria', 'Uber'])
  })

  it('orders the rest alphabetically so the list does not shuffle', () => {
    const rows = filterPayees([payee('Uber'), payee('Amazon'), payee('Padaria')], '')
    expect(names(rows)).toEqual(['Amazon', 'Padaria', 'Uber'])
  })

  it('ignores surrounding whitespace in the search', () => {
    const rows = filterPayees([payee('Uber'), payee('Padaria')], '  uber  ')
    expect(names(rows)).toEqual(['Uber'])
  })

  it('caps the list so a sync-filled workspace does not render thousands of rows', () => {
    const many = Array.from({ length: 500 }, (_, i) => payee(`Payee ${String(i).padStart(3, '0')}`))
    expect(filterPayees(many, '', 200)).toHaveLength(200)
  })

  it('keeps the best matches when it caps', () => {
    const many = [
      ...Array.from({ length: 50 }, (_, i) => payee(`Auto Posto ${i}`)),
      payee('Posto Exato'),
    ]
    expect(names(filterPayees(many, 'posto', 1))).toEqual(['Posto Exato'])
  })
})
