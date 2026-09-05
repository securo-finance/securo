import { describe, expect, it } from 'vitest'
import {
  buildInstallmentSeriesInput,
  hasNonStatusChange,
  isManualInstallmentSeriesRow,
} from './installment-series'
import type { InstallmentSeriesFormInput } from './installment-series'
import type { Transaction, TransactionEditPayload } from '../types'

const base: InstallmentSeriesFormInput = {
  accountId: 'acct-1',
  categoryId: 'cat-1',
  payeeId: null,
  description: 'Notebook',
  amount: '150.00',
  date: '2026-08-06',
  type: 'debit',
  currency: 'BRL',
  notes: '',
  fxFields: { amount_primary: 150, fx_rate_used: 1 },
  splits: null,
  installmentCount: '3',
  installmentFrequency: 'monthly',
  status: 'posted',
}

describe('buildInstallmentSeriesInput', () => {
  it('builds the base payload with per-parcel amount', () => {
    const payload = buildInstallmentSeriesInput(base)
    expect(payload.base).toMatchObject({
      account_id: 'acct-1',
      category_id: 'cat-1',
      payee_id: null,
      description: 'Notebook',
      amount: 150,
      date: '2026-08-06',
      type: 'debit',
      currency: 'BRL',
      notes: null,
    })
    expect(payload.installments).toBe(3)
    expect(payload.first_installment_status).toBe('posted')
    expect(payload.frequency).toBe('monthly')
  })

  it('clamps installments to the backend [2, 360] range', () => {
    expect(buildInstallmentSeriesInput({ ...base, installmentCount: '1' }).installments).toBe(2)
    expect(buildInstallmentSeriesInput({ ...base, installmentCount: '500' }).installments).toBe(360)
    expect(buildInstallmentSeriesInput({ ...base, installmentCount: 'abc' }).installments).toBe(2)
    expect(buildInstallmentSeriesInput({ ...base, installmentCount: '' }).installments).toBe(2)
    expect(buildInstallmentSeriesInput({ ...base, installmentCount: '7' }).installments).toBe(7)
  })

  it('uses the transaction status for the first installment', () => {
    expect(buildInstallmentSeriesInput({ ...base, status: 'posted' }).first_installment_status).toBe('posted')
    expect(buildInstallmentSeriesInput({ ...base, status: 'pending' }).first_installment_status).toBe('pending')
  })

  it('rides the transaction type through, including credit receivables', () => {
    const payload = buildInstallmentSeriesInput({ ...base, type: 'credit' })
    expect(payload.base.type).toBe('credit')
  })

  it('supports the same frequencies as recurring, including quarterly', () => {
    expect(buildInstallmentSeriesInput({ ...base, installmentFrequency: 'monthly' }).frequency).toBe('monthly')
    expect(buildInstallmentSeriesInput({ ...base, installmentFrequency: 'quarterly' }).frequency).toBe('quarterly')
    expect(buildInstallmentSeriesInput({ ...base, installmentFrequency: 'weekly' }).frequency).toBe('weekly')
    expect(buildInstallmentSeriesInput({ ...base, installmentFrequency: 'yearly' }).frequency).toBe('yearly')
    expect(buildInstallmentSeriesInput({ ...base, installmentFrequency: 'biweekly' }).frequency).toBe(
      'biweekly',
    )
    expect(
      buildInstallmentSeriesInput({ ...base, installmentFrequency: 'semiannual' }).frequency,
    ).toBe('semiannual')
  })

  it('omits currency when not set', () => {
    const payload = buildInstallmentSeriesInput({ ...base, currency: undefined })
    expect(payload.base.currency).toBeUndefined()
  })

  it('rides FX overrides through to the base', () => {
    const payload = buildInstallmentSeriesInput({
      ...base,
      fxFields: { amount_primary: 120, fx_rate_used: 0.8 },
    })
    expect(payload.base.amount_primary).toBe(120)
    expect(payload.base.fx_rate_used).toBe(0.8)
  })

  it('rides split-with-group through to the base', () => {
    const splits = {
      share_type: 'equal' as const,
      splits: [{ group_member_id: 'member-1' }],
    }
    const payload = buildInstallmentSeriesInput({ ...base, splits })
    expect(payload.base.splits).toEqual(splits)
  })

  it('normalizes whitespace-only notes to null', () => {
    const payload = buildInstallmentSeriesInput({ ...base, notes: '   ' })
    expect(payload.base.notes).toBeNull()
  })
})

describe('isManualInstallmentSeriesRow', () => {
  it('recognizes multi-installment rows created by the series endpoint', () => {
    expect(isManualInstallmentSeriesRow({
      installment_series_id: 'series-1',
      installment_number: 2,
      total_installments: 3,
    })).toBe(true)
  })

  it('does not recognize bank-synced installment rows', () => {
    expect(isManualInstallmentSeriesRow({
      installment_series_id: null,
      installment_number: 2,
      total_installments: 3,
    })).toBe(false)
  })

  it('keeps recognizing a parcel that bank sync absorbed', () => {
    // The fuzzy manual match flips `source` to "sync" but leaves the series
    // id, so the scope prompt must not vanish for that parcel.
    expect(isManualInstallmentSeriesRow({
      installment_series_id: 'series-1',
      installment_number: 1,
      total_installments: 3,
    })).toBe(true)
  })

  it('does not recognize single-parcel or incomplete installment metadata', () => {
    expect(isManualInstallmentSeriesRow({
      installment_series_id: 'series-1',
      installment_number: 1,
      total_installments: 1,
    })).toBe(false)
    expect(isManualInstallmentSeriesRow({
      installment_series_id: 'series-1',
      installment_number: null,
      total_installments: 3,
    })).toBe(false)
  })
})

// The backend serializes Decimal-backed fields as strings, so the original
// row arrives with amount/amount_primary/fx_rate_used as strings while the
// dialog sends parsed numbers. The comparison must treat those as equal.
const original = {
  id: 'tx-1',
  account_id: 'acct-1',
  category_id: 'cat-1',
  payee_id: null,
  description: 'Notebook',
  amount: '150.00',
  currency: 'BRL',
  date: '2026-08-06',
  type: 'debit',
  status: 'pending',
  notes: null,
  amount_primary: '150.00',
  fx_rate_used: '1',
  effective_bill_date: null,
  is_ignored: false,
  splits: [],
} as unknown as Transaction

// The form dialog always sends a full payload, with status toggled.
const statusOnlyPayload: TransactionEditPayload = {
  description: 'Notebook',
  amount: 150,
  date: '2026-08-06',
  type: 'debit',
  currency: 'BRL',
  category_id: 'cat-1',
  payee_id: null,
  account_id: 'acct-1',
  notes: null,
  is_ignored: false,
  status: 'posted',
}

describe('hasNonStatusChange', () => {
  it('returns false when only the status changed', () => {
    expect(hasNonStatusChange(statusOnlyPayload, original)).toBe(false)
  })

  it('returns true when the amount changed', () => {
    expect(hasNonStatusChange({ ...statusOnlyPayload, amount: 200 }, original)).toBe(true)
  })

  it('returns true when the description changed', () => {
    expect(hasNonStatusChange({ ...statusOnlyPayload, description: 'Renamed' }, original)).toBe(true)
  })

  it('returns true when the currency changed', () => {
    expect(hasNonStatusChange({ ...statusOnlyPayload, currency: 'USD' }, original)).toBe(true)
  })

  it('treats untouched FX overrides as unchanged', () => {
    expect(hasNonStatusChange({
      ...statusOnlyPayload,
      amount_primary: 150,
      fx_rate_used: 1,
    }, original)).toBe(false)
  })

  it('treats null vs undefined ids as unchanged', () => {
    const data = {
      ...statusOnlyPayload,
      payee_id: undefined,
      account_id: undefined,
    }
    expect(hasNonStatusChange(data, { ...original, payee_id: null, account_id: null })).toBe(false)
  })

  it('treats an untouched split group as unchanged', () => {
    const splitOriginal = {
      ...original,
      splits: [
        {
          id: 's1',
          transaction_id: 'tx-1',
          group_member_id: 'member-1',
          share_amount: '50.00',
          share_pct: 33.3333,
          share_type: 'equal',
          notes: null,
          created_at: '2026-08-06T00:00:00Z',
        },
      ],
    } as unknown as Transaction
    const splitPayload = {
      ...statusOnlyPayload,
      splits: {
        share_type: 'equal' as const,
        splits: [{ group_member_id: 'member-1', share_amount: 50, share_pct: 33.3333 }],
      },
    }
    expect(hasNonStatusChange(splitPayload, splitOriginal)).toBe(false)
  })

  it('detects a changed split group', () => {
    const splitOriginal = {
      ...original,
      splits: [
        {
          id: 's1',
          transaction_id: 'tx-1',
          group_member_id: 'member-1',
          share_amount: '50.00',
          share_pct: 33.3333,
          share_type: 'equal',
          notes: null,
          created_at: '2026-08-06T00:00:00Z',
        },
      ],
    } as unknown as Transaction
    const splitPayload = {
      ...statusOnlyPayload,
      splits: {
        share_type: 'equal' as const,
        splits: [{ group_member_id: 'member-1', share_amount: 100, share_pct: 66.6666 }],
      },
    }
    expect(hasNonStatusChange(splitPayload, splitOriginal)).toBe(true)
  })
})
