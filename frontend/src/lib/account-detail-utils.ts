import type { ProjectedTransaction, Transaction } from '../types'

type BalanceTransaction = Pick<
  Transaction,
  'amount' | 'amount_primary' | 'currency' | 'is_ignored' | 'source' | 'type'
>

type MaterializedTransaction = Pick<Transaction, 'date' | 'recurring_transaction_id'>

/** Return the usable amount in the selected currency, or null if FX is unknown. */
export function transactionAmountForBalance(
  transaction: BalanceTransaction,
  usePrimary: boolean,
  displayCurrency: string,
): number | null {
  if (
    usePrimary
    && transaction.amount_primary == null
    && transaction.currency !== displayCurrency
  ) return null

  return usePrimary && transaction.amount_primary != null
    ? Number(transaction.amount_primary)
    : Number(transaction.amount)
}

/** Apply one transaction to a running balance using Account Detail semantics. */
export function applyTransactionToBalance(
  balance: number,
  transaction: BalanceTransaction,
  usePrimary: boolean,
  displayCurrency: string,
): number {
  if (transaction.is_ignored) return balance

  // A missing cross-currency conversion is unknown, not a 1:1 rate. Keep the
  // running balance unchanged until the transaction has a real FX stamp.
  const amount = transactionAmountForBalance(transaction, usePrimary, displayCurrency)
  if (amount == null) return balance
  return balance + (transaction.type === 'credit' ? amount : -amount)
}

/**
 * Hide virtual occurrences that already have a materialized transaction.
 * The recurring link plus the effective occurrence date is authoritative;
 * description and amount can legitimately change after materialization.
 */
export function excludeMaterializedProjections<
  T extends Pick<ProjectedTransaction, 'date' | 'recurring_id'>,
>(
  projections: T[],
  transactions: MaterializedTransaction[],
): T[] {
  const materialized = new Set(
    transactions
      .filter((transaction) => transaction.recurring_transaction_id != null)
      .map((transaction) => `${transaction.recurring_transaction_id}:${transaction.date}`),
  )

  return projections.filter(
    (projection) => !materialized.has(`${projection.recurring_id}:${projection.date}`),
  )
}
