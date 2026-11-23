import { formatCurrency } from '@/lib/format'
import type { ReimbursementFilter, Transaction } from '@/types'

export type ReimbursementSource = Pick<
  Transaction,
  'type' | 'currency' | 'reimbursement_role' | 'reimbursement_net' | 'reimbursement_unallocated'
>

/** A counterpart with nothing left once its allocations are taken off. */
export function isFullyAllocatedReimbursement(tx: ReimbursementSource): boolean {
  return tx.reimbursement_role === 'counterpart' && Number(tx.reimbursement_unallocated ?? 0) <= 0
}

/**
 * The anchor's net, signed the way the list signs amounts. An anchor paid
 * back more than in full flips to the other direction.
 */
export function formatReimbursementNet(tx: ReimbursementSource, locale: string): string | null {
  if (tx.reimbursement_role !== 'anchor' || tx.reimbursement_net == null) return null
  return formatSignedNet(Number(tx.reimbursement_net), tx.type, tx.currency, locale)
}

/** `net`, signed with `type`'s direction (the other one when negative); zero has no sign. */
export function formatSignedNet(net: number, type: string, currency: string, locale: string): string {
  const amount = formatCurrency(Math.abs(net), currency, locale)
  if (Math.abs(net) < 0.005) return amount
  return `${(type === 'credit') === net > 0 ? '+' : '−'}${amount}`
}

const REIMBURSEMENT_FILTERS: ReimbursementFilter[] = ['linked', 'anchor', 'counterpart', 'unlinked']

/** The reimbursement filter as kept in the URL: `?reimbursement=linked|anchor|counterpart|unlinked`. */
export function parseReimbursementFilter(value: string | null): ReimbursementFilter | '' {
  return REIMBURSEMENT_FILTERS.find((filter) => filter === value) ?? ''
}
