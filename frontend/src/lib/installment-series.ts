import type {
  InstallmentSeriesInput,
  Transaction,
  TransactionEditPayload,
  TransactionSplitsInput,
} from '../types'

/**
 * Form-level inputs that the installment section of TransactionForm collects.
 * Mirrors the backend's InstallmentSeriesCreate contract so the payload the
 * UI sends is testable without a DOM.
 */
export interface InstallmentSeriesFormInput {
  accountId: string
  categoryId: string | null
  payeeId: string | null
  description: string
  amount: string
  date: string
  type: 'debit' | 'credit'
  currency?: string
  notes: string
  fxFields: Pick<Partial<Transaction>, 'amount_primary' | 'fx_rate_used'>
  splits: TransactionSplitsInput | null
  installmentCount: string
  installmentFrequency: 'monthly' | 'quarterly' | 'semiannual' | 'weekly' | 'biweekly' | 'yearly'
  status: 'posted' | 'pending'
}

/**
 * Scope prompts are only meaningful for installment series created by the
 * manual installment endpoint. Provider-synced rows may carry installment
 * metadata, but they are independent bank transactions and must be edited
 * or deleted one at a time.
 *
 * The series id is the signal, not `source`: bank sync absorbs a matching
 * manual row by flipping its `source` to "sync" (connection_service's fuzzy
 * manual match), which would otherwise make the prompt disappear from part
 * of a series after the first sync. The series id survives that merge.
 */
export function isManualInstallmentSeriesRow(
  tx:
    | Pick<Transaction, 'installment_series_id' | 'installment_number' | 'total_installments'>
    | null
    | undefined,
): boolean {
  return (
    tx?.installment_series_id != null &&
    tx.installment_number != null &&
    tx.total_installments != null &&
    tx.total_installments > 1
  )
}

/**
 * Build the POST /api/transactions/installments payload from the form.
 *
 * "Repeat as installments": the transaction is repeated N times, so
 * `base.amount` is the per-parcel amount and the backend derives the series
 * total as `amount * installments` (no separate total is sent). The first
 * parcel carries the transaction's own status; subsequent ones are "pending".
 *
 * - `installments` is clamped to the backend's [2, 360] range (empty or
 *   invalid input falls back to 2).
 * - Split-with-group and FX overrides ride along on the base so every parcel
 *   is finalized exactly like a single manual transaction.
 */
export function buildInstallmentSeriesInput(
  input: InstallmentSeriesFormInput,
): InstallmentSeriesInput {
  const installments = Math.min(
    Math.max(parseInt(input.installmentCount, 10) || 2, 2),
    360,
  )
  return {
    base: {
      account_id: input.accountId,
      category_id: input.categoryId || null,
      payee_id: input.payeeId || null,
      description: input.description,
      amount: parseFloat(input.amount),
      date: input.date,
      type: input.type,
      ...(input.currency ? { currency: input.currency } : {}),
      notes: input.notes.trim() || null,
      ...input.fxFields,
      ...(input.splits ? { splits: input.splits } : {}),
    },
    installments,
    first_installment_status: input.status,
    frequency: input.installmentFrequency,
  }
}

/**
 * True when an edit payload touches any field beyond the status toggle.
 *
 * The dialog sends a full form payload on save, while the backend
 * serializes Decimal-backed fields (amount, amount_primary, fx_rate_used)
 * as strings, so values are compared leniently: numeric fields are coerced
 * and splits are normalized before comparison. Status-only edits skip the
 * installment-series scope prompt.
 */
export function hasNonStatusChange(
  data: TransactionEditPayload,
  original: Transaction,
): boolean {
  const numericKeys = new Set([
    'amount',
    'amount_primary',
    'fx_rate_used',
    'installment_total_amount',
  ])
  const toNumber = (v: unknown): number | null => {
    if (v == null || v === '') return null
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }
  const normalizeSplits = (v: TransactionSplitsInput | null | undefined) => {
    if (!v || v.splits.length === 0) return null
    return {
      share_type: v.share_type,
      splits: v.splits.map((s) => ({
        group_member_id: s.group_member_id,
        share_amount: toNumber(s.share_amount),
        share_pct: toNumber(s.share_pct),
      })),
    }
  }
  const originalSplits: TransactionSplitsInput | null = original.splits?.length
    ? {
        share_type: (original.splits[0].share_type as TransactionSplitsInput['share_type']) ?? 'equal',
        splits: original.splits.map((s) => ({
          group_member_id: s.group_member_id,
          share_amount: s.share_amount,
          share_pct: s.share_pct,
        })),
      }
    : null

  return Object.entries(data).some(([key, value]) => {
    if (key === 'status' || key === 'apply_to' || key === 'apply_to_transfer_pair') {
      return false
    }
    const originalValue = original[key as keyof Transaction]
    if (numericKeys.has(key)) {
      return toNumber(value) !== toNumber(originalValue)
    }
    if (value === originalValue) return false
    if (value == null && originalValue == null) return false
    if (key === 'splits') {
      return (
        JSON.stringify(normalizeSplits(value as TransactionSplitsInput | null)) !==
        JSON.stringify(normalizeSplits(originalSplits))
      )
    }
    return true
  })
}
