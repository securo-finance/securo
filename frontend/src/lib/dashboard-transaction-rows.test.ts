import { describe, expect, it } from 'vitest'
import { buildDashboardTransactionRows, expandDashboardRows } from './dashboard-transaction-rows'
import type { SimilarTransactionGroup, Transaction } from '../types'

function transaction(id: string, date: string): Transaction {
  return {
    id,
    description: 'Coffee',
    date,
    type: 'debit',
    amount: 5,
    amount_primary: 5,
    currency: 'USD',
    account_id: 'account-1',
    category: null,
    category_id: null,
    attachment_count: 0,
    is_shared: false,
    is_ignored: false,
    installment_number: null,
    total_installments: null,
    viewer_share: null,
    group_id: null,
    parent_owner_name: null,
    status: 'posted',
    source: 'manual',
  } as Transaction
}

function group(): SimilarTransactionGroup {
  const transactions = [transaction('one', '2026-08-01'), transaction('two', '2026-08-20')]
  return {
    kind: 'group',
    key: 'coffee',
    transactions,
    description: 'Coffee',
    type: 'debit',
    currency: 'USD',
    total_amount: 10,
    amount_primary: 10,
    parent_total: null,
    owner_share: null,
    earliest_date: '2026-08-01',
    latest_date: '2026-08-20',
    account_id: 'account-1',
    category_id: null,
    category: null,
    has_multiple_categories: false,
    common_notes: null,
    has_notes: false,
    has_multiple_notes: false,
    attachment_count: 0,
    status: 'posted',
    is_ignored: false,
    is_transfer: false,
    is_shared: false,
    group_id: null,
    parent_owner_name: null,
    has_pending_badge: false,
    has_fx_fallback: false,
  }
}

describe('buildDashboardTransactionRows', () => {
  it('maps server groups once and merges projected rows by date', () => {
    const rows = buildDashboardTransactionRows(
      [group()],
      [{
        recurring_id: 'rent',
        account_id: 'account-1',
        description: 'Rent',
        amount: 100,
        amount_primary: 100,
        currency: 'USD',
        type: 'debit',
        date: '2026-08-25',
        category_id: null,
        category_name: null,
        category_icon: null,
        category_color: null,
      }],
      true,
      new Map(),
    )

    expect(rows.map(row => row.kind)).toEqual(['projected', 'group'])
    expect(rows[1]).toMatchObject({ amount: 10, date: '2026-08-20' })
  })

  it('expands children directly from the group row', () => {
    const rows = buildDashboardTransactionRows(
      [group()],
      [],
      true,
      new Map(),
    )

    expect(expandDashboardRows(rows, new Set(['coffee']))).toHaveLength(3)
  })
})
