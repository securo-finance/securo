import type { Transaction } from '@/types'

/**
 * Whether a transaction reads as a transfer in row-level indicators.
 *
 * A transaction is a transfer when it is part of a linked transfer pair, or
 * when its category is flagged `treat_as_transfer`. The backend classifies
 * transfers the same way for profit and loss: the dashboard, the reports and
 * the list summary all keep these rows out of income and expense totals, and
 * the calendar marks each item with the same test. Row indicators must match,
 * so a row that the totals keep out reads as a transfer on every screen that
 * lists it (issue #993).
 */
export function isTransferTransaction(
  tx: Pick<Transaction, 'transfer_pair_id' | 'category'>,
): boolean {
  return !!tx.transfer_pair_id || !!tx.category?.treat_as_transfer
}
