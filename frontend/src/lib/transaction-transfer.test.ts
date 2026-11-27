import { describe, expect, it } from 'vitest'
import { isTransferTransaction } from './transaction-transfer'
import type { Category, Transaction } from '@/types'

function makeCategory(overrides: Partial<Category> = {}): Category {
  return {
    id: 'cat-1',
    user_id: 'u-1',
    group_id: null,
    name: 'Food',
    icon: 'utensils',
    color: '#f97316',
    is_system: false,
    is_hidden: false,
    treat_as_transfer: false,
    is_ignored: false,
    ...overrides,
  }
}

function makeTx(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: 'tx-1',
    user_id: 'u-1',
    account_id: 'acc-1',
    category_id: null,
    category: null,
    external_id: null,
    description: 'STORE PURCHASE',
    original_description: null,
    amount: -42,
    currency: 'USD',
    date: '2026-10-20',
    type: 'debit',
    source: 'manual',
    status: 'posted',
    payee: 'Store A',
    payee_id: null,
    payee_name: null,
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
    reporting_date_override: overrides.reporting_date_override ?? null,
  }
}

describe('isTransferTransaction', () => {
  it('reads a linked transfer pair as a transfer', () => {
    expect(isTransferTransaction(makeTx({ transfer_pair_id: 'pair-1' }))).toBe(true)
  })

  it('reads a treat_as_transfer category as a transfer without a pair (#993)', () => {
    const category = makeCategory({ treat_as_transfer: true })
    expect(
      isTransferTransaction(makeTx({ category_id: category.id, category })),
    ).toBe(true)
  })

  it('does not read a normal category as a transfer', () => {
    const category = makeCategory({ treat_as_transfer: false })
    expect(
      isTransferTransaction(makeTx({ category_id: category.id, category })),
    ).toBe(false)
  })

  it('does not read an uncategorized row without a pair as a transfer', () => {
    expect(isTransferTransaction(makeTx())).toBe(false)
  })

  it('does not read an unlinked source transfer row as a transfer (#993)', () => {
    expect(isTransferTransaction(makeTx({ source: 'transfer' }))).toBe(false)
  })

  it('reads a split whose lines are all transfer categories as a transfer (#993)', () => {
    const category = makeCategory({ treat_as_transfer: true })
    const tx = makeTx({
      has_category_lines: true,
      category_lines: [
        {
          id: 'line-1', category_id: category.id, amount: 30,
          share_type: 'exact', share_pct: null, position: 0, notes: null,
          category,
        },
        {
          id: 'line-2', category_id: category.id, amount: 12,
          share_type: 'exact', share_pct: null, position: 1, notes: null,
          category,
        },
      ],
    })
    expect(isTransferTransaction(tx)).toBe(true)
  })

  it('does not read a split with one expense line as a transfer (#993)', () => {
    const transfer = makeCategory({ id: 'cat-t', treat_as_transfer: true })
    const expense = makeCategory({ id: 'cat-e', treat_as_transfer: false })
    const tx = makeTx({
      has_category_lines: true,
      category_lines: [
        {
          id: 'line-1', category_id: transfer.id, amount: 30,
          share_type: 'exact', share_pct: null, position: 0, notes: null,
          category: transfer,
        },
        {
          id: 'line-2', category_id: expense.id, amount: 12,
          share_type: 'exact', share_pct: null, position: 1, notes: null,
          category: expense,
        },
      ],
    })
    expect(isTransferTransaction(tx)).toBe(false)
  })

  it('does not read a split whose lines are all ignored as a transfer (#993)', () => {
    const hidden = makeCategory({ treat_as_transfer: true, is_ignored: true })
    const tx = makeTx({
      has_category_lines: true,
      category_lines: [
        {
          id: 'line-1', category_id: hidden.id, amount: 30,
          share_type: 'exact', share_pct: null, position: 0, notes: null,
          category: hidden,
        },
      ],
    })
    expect(isTransferTransaction(tx)).toBe(false)
  })
})
