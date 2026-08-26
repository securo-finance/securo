import { describe, expect, it } from 'vitest'

import {
  applyTransactionToBalance,
  excludeMaterializedProjections,
} from './account-detail-utils'

describe('applyTransactionToBalance', () => {
  it('does not change the balance for ignored transactions', () => {
    expect(applyTransactionToBalance(100, {
      amount: 30,
      amount_primary: null,
      currency: 'BRL',
      is_ignored: true,
      source: 'manual',
      type: 'debit',
    }, false, 'BRL')).toBe(100)
  })

  it('uses the selected currency amount for active transactions', () => {
    const transaction = {
      amount: 10,
      amount_primary: 50,
      currency: 'USD',
      is_ignored: false,
      source: 'manual',
      type: 'credit' as const,
    }

    expect(applyTransactionToBalance(100, transaction, false, 'USD')).toBe(110)
    expect(applyTransactionToBalance(100, transaction, true, 'BRL')).toBe(150)
  })

  it('does not treat a missing cross-currency conversion as 1:1', () => {
    const transaction = {
      amount: 13037.13,
      amount_primary: null,
      currency: 'USD',
      is_ignored: false,
      source: 'manual',
      type: 'credit' as const,
    }

    expect(applyTransactionToBalance(8925.76, transaction, true, 'BRL')).toBe(8925.76)
  })

  it('applies an opening balance row when it is inside the visible period', () => {
    expect(applyTransactionToBalance(100, {
      amount: 500,
      amount_primary: null,
      currency: 'BRL',
      is_ignored: false,
      source: 'opening_balance',
      type: 'credit',
    }, false, 'BRL')).toBe(600)
  })
})

describe('excludeMaterializedProjections', () => {
  it('removes only the occurrence already linked and materialized on that date', () => {
    const projections = [
      { recurring_id: 'rec-1', date: '2026-08-25' },
      { recurring_id: 'rec-1', date: '2026-09-25' },
      { recurring_id: 'rec-2', date: '2026-08-25' },
    ]

    const result = excludeMaterializedProjections(projections, [
      { recurring_transaction_id: 'rec-1', date: '2026-08-25' },
      { recurring_transaction_id: null, date: '2026-09-25' },
    ])

    expect(result).toEqual([projections[1], projections[2]])
  })
})
