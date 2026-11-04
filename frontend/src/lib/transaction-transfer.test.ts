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
})
