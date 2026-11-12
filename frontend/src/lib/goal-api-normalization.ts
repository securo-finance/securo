import type { Goal, GoalAllocation, GoalSummary } from '@/types'

type ApiDecimal = number | string

export type ApiGoal = Omit<
  Goal,
  | 'target_amount'
  | 'current_amount'
  | 'target_amount_primary'
  | 'current_amount_primary'
  | 'account_balance'
  | 'account_reserved_total'
  | 'account_available'
> & {
  target_amount: ApiDecimal
  current_amount: ApiDecimal
  target_amount_primary: ApiDecimal | null
  current_amount_primary: ApiDecimal | null
  account_balance?: ApiDecimal | null
  account_reserved_total?: ApiDecimal | null
  account_available?: ApiDecimal | null
}

export type ApiGoalSummary = Omit<GoalSummary, 'target_amount' | 'current_amount'> & {
  target_amount: ApiDecimal
  current_amount: ApiDecimal
}

export type ApiGoalAllocation = Omit<GoalAllocation, 'amount'> & {
  amount: ApiDecimal
}

function normalizeDecimal(value: ApiDecimal, field: string): number {
  const normalized = Number(value)
  if (!Number.isFinite(normalized)) {
    throw new TypeError(`Invalid decimal value for ${field}`)
  }
  return normalized
}

function normalizeOptionalDecimal(
  value: ApiDecimal | null | undefined,
  field: string,
): number | null | undefined {
  return value == null ? value : normalizeDecimal(value, field)
}

export function normalizeGoal(goal: ApiGoal): Goal {
  return {
    ...goal,
    target_amount: normalizeDecimal(goal.target_amount, 'goal.target_amount'),
    current_amount: normalizeDecimal(goal.current_amount, 'goal.current_amount'),
    target_amount_primary: normalizeOptionalDecimal(
      goal.target_amount_primary,
      'goal.target_amount_primary',
    ) ?? null,
    current_amount_primary: normalizeOptionalDecimal(
      goal.current_amount_primary,
      'goal.current_amount_primary',
    ) ?? null,
    account_balance: normalizeOptionalDecimal(goal.account_balance, 'goal.account_balance'),
    account_reserved_total: normalizeOptionalDecimal(
      goal.account_reserved_total,
      'goal.account_reserved_total',
    ),
    account_available: normalizeOptionalDecimal(
      goal.account_available,
      'goal.account_available',
    ),
  }
}

export function normalizeGoalSummary(summary: ApiGoalSummary): GoalSummary {
  return {
    ...summary,
    target_amount: normalizeDecimal(summary.target_amount, 'goalSummary.target_amount'),
    current_amount: normalizeDecimal(summary.current_amount, 'goalSummary.current_amount'),
  }
}

export function normalizeGoalAllocation(allocation: ApiGoalAllocation): GoalAllocation {
  return {
    ...allocation,
    amount: normalizeDecimal(allocation.amount, 'goalAllocation.amount'),
  }
}
