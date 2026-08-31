import { describe, expect, it } from 'vitest'
import type { SimilarTransactionGroup, Transaction, TransactionDisplayItem } from '../types'
import {
  flattenTransactionDisplayItems,
  formatSimilarTransactionDateRange,
  groupSimilarTransactions,
  similarGroupSelectionState,
  visibleTransactionRows,
} from './transaction-grouping'

function transaction(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: 'tx-1',
    user_id: 'user-1',
    account_id: 'account-1',
    category_id: null,
    category: null,
    external_id: null,
    description: 'Daily yield',
    original_description: null,
    amount: 1.5,
    currency: 'BRL',
    date: '2026-08-01',
    type: 'credit',
    source: 'manual',
    status: 'posted',
    payee: null,
    payee_id: 'payee-1',
    payee_name: 'Mercado Pago',
    notes: null,
    transfer_pair_id: null,
    amount_primary: null,
    fx_rate_used: null,
    fx_fallback: false,
    installment_number: null,
    total_installments: null,
    installment_total_amount: null,
    installment_purchase_date: null,
    installment_series_id: null,
    bill_id: null,
    effective_bill_date: null,
    splits: [],
    is_ignored: false,
    ...overrides,
  }
}

function group(transactions: Transaction[]): SimilarTransactionGroup {
  return {
    kind: 'group',
    key: 'group-1',
    transactions,
    description: 'Daily yield',
    type: 'credit',
    currency: 'BRL',
    total_amount: transactions.reduce((total, item) => total + Math.abs(item.amount), 0),
    amount_primary: null,
    parent_total: null,
    owner_share: null,
    earliest_date: '2026-08-01',
    latest_date: '2026-08-25',
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

describe('formatSimilarTransactionDateRange', () => {
  it('uses a compact locale-aware range within the reference year', () => {
    const formatted = formatSimilarTransactionDateRange(
      { earliest_date: '2026-08-01', latest_date: '2026-08-25' },
      'en-US',
      new Date('2026-01-01T00:00:00Z'),
    )

    expect(formatted).toMatch(/Aug/)
    expect(formatted).toMatch(/1/)
    expect(formatted).toMatch(/25/)
    expect(formatted).not.toMatch(/2026/)
  })

  it('keeps the year visible outside the reference year', () => {
    const formatted = formatSimilarTransactionDateRange(
      { earliest_date: '2020-08-01', latest_date: '2020-08-25' },
      'en-US',
      new Date('2026-01-01T00:00:00Z'),
    )

    expect(formatted).toContain('2020')
  })
})

describe('transaction display helpers', () => {
  const first = transaction({ id: 'first' })
  const second = transaction({ id: 'second', date: '2026-08-25' })
  const other = transaction({ id: 'other', description: 'Coffee' })
  const items: TransactionDisplayItem[] = [
    group([first, second]),
    { kind: 'transaction', transaction: other },
  ]

  it('flattens all children for selection and transaction actions', () => {
    expect(flattenTransactionDisplayItems(items).map(item => item.id)).toEqual([
      'first',
      'second',
      'other',
    ])
  })

  it('uses the rendered leaf order for range selection', () => {
    expect(visibleTransactionRows(items, new Set()).map(item => item.id)).toEqual(['other'])
    expect(visibleTransactionRows(items, new Set(['group-1'])).map(item => item.id)).toEqual([
      'first',
      'second',
      'other',
    ])
  })

  it('reports none, some, and all selectable children', () => {
    const item = group([first, second])
    expect(similarGroupSelectionState(item, new Set())).toBe('none')
    expect(similarGroupSelectionState(item, new Set(['first']))).toBe('some')
    expect(similarGroupSelectionState(item, new Set(['first', 'second']))).toBe('all')
  })

  it('ignores shared children when calculating group selection', () => {
    const shared = transaction({ id: 'shared', is_shared: true })
    const item = group([first, shared])
    expect(similarGroupSelectionState(item, new Set(['first']))).toBe('all')
  })
})

describe('groupSimilarTransactions', () => {
  it('groups every matching transaction supplied by the current page', () => {
    const transactions = Array.from({ length: 31 }, (_, index) => transaction({
      id: `transaction-${index}`,
      date: `2026-08-${String(index + 1).padStart(2, '0')}`,
    }))

    const items = groupSimilarTransactions(transactions, true, 'date', 'desc')

    expect(items).toHaveLength(1)
    expect(items[0].kind).toBe('group')
    if (items[0].kind !== 'group') return
    expect(items[0].transactions).toHaveLength(31)
    expect(items[0].total_amount).toBe(46.5)
  })

  it('sorts collapsed rows by their visible aggregate amount', () => {
    const items = groupSimilarTransactions([
      transaction({ id: 'group-a', amount: 60 }),
      transaction({ id: 'group-b', amount: 60 }),
      transaction({ id: 'single', description: 'Other', payee_id: 'other', amount: 100 }),
    ], true, 'amount', 'asc')

    expect(items.map(item => item.kind)).toEqual(['transaction', 'group'])
  })

  it('does not merge rows with different financial or sharing state', () => {
    const items = groupSimilarTransactions([
      transaction({ id: 'regular' }),
      transaction({ id: 'ignored', is_ignored: true }),
      transaction({ id: 'pending', status: 'pending' }),
      transaction({ id: 'transfer', transfer_pair_id: 'pair' }),
      transaction({ id: 'shared', is_shared: true, viewer_share: 1 }),
    ], true)

    expect(items).toHaveLength(5)
    expect(items.every(item => item.kind === 'transaction')).toBe(true)
  })

  it('normalizes Unicode, case, and whitespace in matching text', () => {
    const items = groupSimilarTransactions([
      transaction({ id: 'one', description: 'ＤＡＩＬＹ  Yield', payee_id: null, payee_name: ' Mercado Pago ' }),
      transaction({ id: 'two', description: 'daily yield', payee_id: null, payee_name: 'mercado pago' }),
    ], true)

    expect(items).toHaveLength(1)
    expect(items[0].kind).toBe('group')
  })
})
