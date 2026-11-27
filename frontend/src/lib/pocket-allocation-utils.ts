import type { GoalAllocationInput } from '@/types'

export function pocketAllocationsAreValid(
  allocations: GoalAllocationInput[],
  transactionAmount: number,
): boolean {
  if (allocations.length === 0) return true
  if (!Number.isFinite(transactionAmount) || transactionAmount <= 0) return false
  const total = allocations.reduce((sum, item) => sum + item.amount, 0)
  return allocations.every(item => item.amount > 0)
    && total <= Math.abs(transactionAmount) + 0.0001
}

const toCents = (amount: number) => Math.round(amount * 100)
// `+ 0` turns -0 into 0, so an exactly reserved account never shows "-0.00".
const fromCents = (cents: number) => cents / 100 + 0

/** Pocket amounts added up in whole cents, free of float drift. */
export function sumPocketAmounts(amounts: number[]): number {
  return fromCents(amounts.reduce((sum, amount) => sum + toCents(amount), 0))
}

/** What an account has left once its pockets are reserved, in whole cents. */
export function pocketAvailable(balance: number, reserved: number): number {
  return fromCents(toCents(balance) - toCents(reserved))
}

/** An amount rounded to the cents the API accepts. */
export function roundToCents(amount: number): number {
  return fromCents(toCents(amount))
}

/**
 * The most a withdrawal may take from a pocket: what the pocket holds plus
 * what this transaction already takes from it, never more than the
 * transaction itself. `persisted` is the saved, signed assignment (negative
 * for a withdrawal), which the pocket's current amount already includes.
 */
export function withdrawalCap(current: number, persisted: number, transactionAmount: number): number {
  const available = toCents(current) - toCents(persisted)
  return fromCents(Math.max(0, Math.min(available, toCents(Math.abs(transactionAmount)))))
}
