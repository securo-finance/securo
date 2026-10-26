import { addDays, addYears, format, parseISO, subYears } from 'date-fns'

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

type ServerBalanceRow = Pick<Transaction, 'date' | 'running_balance' | 'running_balance_primary'>

type ProjectionRow = BalanceTransaction & Pick<Transaction, 'date'>

/**
 * Give every loaded row and recurring projection its running balance, newest
 * first (#1202).
 *
 * `rows` are real transactions in API order (newest first, possibly only the
 * first pages) carrying the server's balance right after each of them. The
 * server walks the whole range, so a row's balance never depends on which
 * pages are loaded; projections are the only thing added on top.
 *
 * Projections keep the placement the walk always gave them: by date, after
 * the real rows of the same date. So a real row adds every projection dated
 * before it, and a projection starts from the newest real row dated on or
 * before it (or `openingBalance` when the range has none) and adds every
 * projection up to and including itself. While older pages remain, a
 * projection with no loaded row on or before its date is left out: its
 * starting point is on a page not fetched yet.
 */
export function mergeRunningBalances<R extends ServerBalanceRow, P extends ProjectionRow>(
  rows: R[],
  projections: P[],
  options: {
    openingBalance: number
    hasMore: boolean
    usePrimary: boolean
    displayCurrency: string
  },
): Array<(R | P) & { runningBalance: number }> {
  const { openingBalance, hasMore, usePrimary, displayCurrency } = options
  // The page only lists rows the server walked, so a null here is not expected.
  const serverBalance = (row: R) => (
    usePrimary ? row.running_balance_primary ?? row.running_balance : row.running_balance
  ) ?? 0

  // Stable, so same-day projections keep the order they came in.
  const ascending = [...projections].sort((a, b) => a.date.localeCompare(b.date))
  let total = 0
  const projectedUpTo = ascending.map((projection) => (
    total = applyTransactionToBalance(total, projection, usePrimary, displayCurrency)
  ))

  const merged: Array<(R | P) & { runningBalance: number }> = []
  let next = ascending.length - 1
  for (let i = 0; i <= rows.length; i++) {
    const row = rows[i] as R | undefined
    // Projections on or after this row's date sit above it, and this row is
    // the newest real row on or before each of them.
    for (; next >= 0 && (!row || ascending[next].date >= row.date); next--) {
      if (!row && hasMore) continue
      const start = row ? serverBalance(row) : openingBalance
      merged.push({ ...ascending[next], runningBalance: start + projectedUpTo[next] })
    }
    if (row) {
      merged.push({ ...row, runningBalance: serverBalance(row) + (next >= 0 ? projectedUpTo[next] : 0) })
    }
  }
  return merged
}

// Mirrors the backend's `_MAX_RUNNING_BALANCE_YEARS` (backend/app/api/accounts.py),
// in calendar years like the reports' custom range.
const RUNNING_BALANCE_MAX_YEARS = 10

/**
 * First day the chart asks the running-balance series for. A range longer than
 * the endpoint allows charts its latest part; the list still covers it all.
 */
export function runningBalanceChartFrom(from: string, to: string): string {
  if (!from || !to) return from
  const end = parseISO(to)
  let earliest = subYears(end, RUNNING_BALANCE_MAX_YEARS)
  // Ten years back from a Feb 29 lands on Feb 28, and the backend's year step
  // from there stops a day short of `to`.
  if (addYears(earliest, RUNNING_BALANCE_MAX_YEARS) < end) earliest = addDays(earliest, 1)
  const earliestDay = format(earliest, 'yyyy-MM-dd')
  return from < earliestDay ? earliestDay : from
}
