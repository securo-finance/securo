import type { Transaction } from '@/types'

export type DatedTransaction = Pick<Transaction, 'date' | 'reporting_date_override'> & {
  reporting_date?: string | null
}

/**
 * The date the list filters and orders a row by: the server's
 * `reporting_date` (override, manual bill date, or a card's bill due date in
 * accrual mode), else the override, else the bank date (#1386).
 */
export function listDate(tx: DatedTransaction): string {
  return tx.reporting_date ?? tx.reporting_date_override ?? tx.date
}
