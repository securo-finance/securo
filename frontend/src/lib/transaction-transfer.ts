import type { Transaction } from '@/types'

/**
 * Whether a transaction reads as a transfer in row-level indicators.
 *
 * A transaction is a transfer when one of these holds:
 * - it is part of a linked transfer pair;
 * - every part of it that still counts sits in a category flagged
 *   `treat_as_transfer`: its category on a plain row, every line on a split.
 *   Lines in ignored categories do not count, as the totals leave them out.
 *
 * The backend classifies transfers the same way for profit and loss and for
 * the calendar badge, so a row the totals keep out reads as a transfer on
 * every screen that lists it, and a row they count as income or expense never
 * does (issue #993).
 *
 * `source === 'transfer'` alone is not enough. A transfer is always written as
 * two legs, so a `source` row with no pair is a leg whose pair was unlinked or
 * whose other side was deleted; the profit and loss figures count it as income
 * or expense, and the summary of the same screen would then disagree with the
 * badge.
 */
export function isTransferTransaction(
  tx: Pick<
    Transaction,
    'transfer_pair_id' | 'category' | 'has_category_lines' | 'category_lines'
  >,
): boolean {
  if (tx.transfer_pair_id) return true
  if (tx.has_category_lines && tx.category_lines?.length) {
    // The split's own category is null, so its parts are what decide. Same
    // rule as the calendar's item badge: all of what is left must be a
    // transfer, and something must be left.
    const counted = tx.category_lines.filter((line) => !line.category?.is_ignored)
    return counted.length > 0 && counted.every((line) => !!line.category?.treat_as_transfer)
  }
  return !!tx.category?.treat_as_transfer
}
