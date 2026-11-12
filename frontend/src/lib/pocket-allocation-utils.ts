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
