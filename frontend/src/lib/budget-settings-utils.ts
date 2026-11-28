import type {
  Budget,
  BudgetForecastResponse,
  BudgetMultiMonthForecastItem,
  BudgetRolloverCategory,
  BudgetRolloverSummary,
  BudgetVsActual,
} from '@/types'
import { parseAmountInput } from '@/lib/format'

type DecimalAmount = number | string
type BudgetVsActualApiRow = Omit<
  BudgetVsActual,
  | 'budget_amount'
  | 'actual_amount'
  | 'projected_amount'
  | 'prev_month_amount'
  | 'projected_prev_month_amount'
  | 'percentage_used'
  | 'carryover_amount'
  | 'effective_budget_amount'
> & {
  budget_amount: DecimalAmount | null
  actual_amount: DecimalAmount
  projected_amount: DecimalAmount
  prev_month_amount: DecimalAmount
  projected_prev_month_amount: DecimalAmount
  percentage_used: DecimalAmount | null
  rollover?: boolean
  reset_rollover?: boolean
  carryover_amount?: DecimalAmount
  effective_budget_amount?: DecimalAmount | null
}

type BudgetRolloverCategoryApiRow = Omit<
  BudgetRolloverCategory,
  'budgeted' | 'actual_spent' | 'projected_spent' | 'remaining' | 'carryover_in' | 'effective_budget'
> & {
  budgeted: DecimalAmount
  actual_spent: DecimalAmount
  projected_spent: DecimalAmount
  remaining: DecimalAmount
  carryover_in?: DecimalAmount
  effective_budget?: DecimalAmount
  rollover_enabled?: boolean
  reset_rollover?: boolean
}

type BudgetRolloverSummaryApiResponse = Omit<
  BudgetRolloverSummary,
  'total_budgeted' | 'total_spent' | 'total_projected_spent' | 'net_surplus' | 'total_carryover' | 'total_effective_budget' | 'categories'
> & {
  total_budgeted: DecimalAmount
  total_spent: DecimalAmount
  total_projected_spent: DecimalAmount
  net_surplus: DecimalAmount
  total_carryover?: DecimalAmount
  total_effective_budget?: DecimalAmount
  categories: BudgetRolloverCategoryApiRow[]
}

type BudgetForecastApiItem = Omit<
  BudgetMultiMonthForecastItem,
  'projected_budget' | 'projected_spend' | 'projected_variance' | 'carryover_balance' | 'effective_budget'
> & {
  projected_budget: DecimalAmount
  projected_spend: DecimalAmount
  projected_variance: DecimalAmount
  carryover_balance?: DecimalAmount
  effective_budget?: DecimalAmount
}

type BudgetForecastApiResponse = {
  forecast_months: number
  items: BudgetForecastApiItem[]
}

export function normalizeBudgetComparisonRows(rows: BudgetVsActualApiRow[]): BudgetVsActual[] {
  return rows.map((row) => {
    const budgetAmount = row.budget_amount === null ? null : Number(row.budget_amount)
    const carryoverAmount = row.carryover_amount === undefined ? 0 : Number(row.carryover_amount)
    const effectiveBudgetAmount =
      row.effective_budget_amount === undefined
        ? budgetAmount === null
          ? null
          : budgetAmount + carryoverAmount
        : row.effective_budget_amount === null
          ? null
          : Number(row.effective_budget_amount)
    return {
      ...row,
      budget_amount: budgetAmount,
      actual_amount: Number(row.actual_amount),
      projected_amount: Number(row.projected_amount),
      prev_month_amount: Number(row.prev_month_amount),
      projected_prev_month_amount: Number(row.projected_prev_month_amount),
      percentage_used: row.percentage_used === null ? null : Number(row.percentage_used),
      rollover: Boolean(row.rollover),
      reset_rollover: Boolean(row.reset_rollover),
      carryover_amount: carryoverAmount,
      effective_budget_amount: effectiveBudgetAmount,
    }
  })
}

export function normalizeBudgetRolloverSummary(
  data: BudgetRolloverSummaryApiResponse,
): BudgetRolloverSummary {
  const totalBudgeted = Number(data.total_budgeted)
  const totalCarryover = data.total_carryover === undefined ? 0 : Number(data.total_carryover)
  return {
    month: data.month,
    total_budgeted: totalBudgeted,
    total_spent: Number(data.total_spent),
    total_projected_spent: Number(data.total_projected_spent),
    net_surplus: Number(data.net_surplus),
    total_carryover: totalCarryover,
    total_effective_budget:
      data.total_effective_budget === undefined
        ? totalBudgeted + totalCarryover
        : Number(data.total_effective_budget),
    categories: (data.categories ?? []).map((c) => {
      const budgeted = Number(c.budgeted)
      const carryoverIn = c.carryover_in === undefined ? 0 : Number(c.carryover_in)
      return {
        category_id: c.category_id,
        category_name: c.category_name,
        budgeted,
        actual_spent: Number(c.actual_spent),
        projected_spent: Number(c.projected_spent),
        remaining: Number(c.remaining),
        carryover_eligible: Boolean(c.carryover_eligible),
        rollover_enabled: Boolean(c.rollover_enabled),
        reset_rollover: Boolean(c.reset_rollover),
        carryover_in: carryoverIn,
        effective_budget:
          c.effective_budget === undefined ? budgeted + carryoverIn : Number(c.effective_budget),
      }
    }),
  }
}

export function normalizeBudgetForecast(data: BudgetForecastApiResponse): BudgetForecastResponse {
  return {
    forecast_months: Number(data.forecast_months),
    items: (data.items ?? []).map((item) => {
      const projectedBudget = Number(item.projected_budget)
      const carryoverBalance =
        item.carryover_balance === undefined ? 0 : Number(item.carryover_balance)
      return {
        month: item.month,
        projected_budget: projectedBudget,
        projected_spend: Number(item.projected_spend),
        projected_variance: Number(item.projected_variance),
        carryover_balance: carryoverBalance,
        effective_budget:
          item.effective_budget === undefined
            ? projectedBudget + carryoverBalance
            : Number(item.effective_budget),
      }
    }),
  }
}

/** A budget's limit in the viewer's display currency (the API sends decimals as strings). */
export function budgetDisplayAmount(budget: Budget): number {
  return Number(budget.amount_primary ?? budget.amount)
}

export type BudgetOperation =
  | {
      type: 'create'
      data: {
        category_id: string
        amount: number
        month: string
        is_recurring: boolean
        rollover?: boolean
        reset_rollover?: boolean
      }
    }
  | {
      type: 'update'
      id: string
      data: {
        amount?: number
        effective_month?: string
        rollover?: boolean
        reset_rollover?: boolean
      }
    }
  | { type: 'delete'; id: string }

/** Empty or zero removes a budget; invalid and negative values are rejected. */
export function parseBudgetDraft(draft: string, locale = 'en-US'): number | null {
  if (!draft.trim()) return 0
  const amount = parseAmountInput(draft, locale)
  if (amount === null || amount < 0) return null
  return Math.round(amount * 100) / 100
}

function nextMonth(month: string): string {
  const [year, monthNumber] = month.split('-').map(Number)
  const next = new Date(year, monthNumber, 1)
  return `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}-01`
}

/**
 * The API calls that give `categoryId` the budget `amount` in `month`
 * (`YYYY-MM-01`), repeating every month or only this month. `current` is the
 * budget that applies to that month today: a month-only budget or the
 * recurring budget it inherits.
 *
 * A recurring budget changed from this month on keeps earlier months as they
 * were (`effective_month`). Zero-valued rows are overrides/tombstones, so
 * clearing one deletes the override and restores whatever budget it replaced.
 */
export function planBudgetChange({
  categoryId,
  amount,
  recurring,
  rollover = false,
  resetRollover = false,
  current,
  series = current ? [current] : [],
  month,
}: {
  categoryId: string
  amount: number
  recurring: boolean
  rollover?: boolean
  resetRollover?: boolean
  current: Budget | undefined
  series?: Budget[]
  month: string
}): BudgetOperation[] {
  // What the editor shows, so an untouched limit plans no change.
  const currentAmount = current ? budgetDisplayAmount(current) : 0
  const currentRollover = current?.rollover ?? false
  if (
    amount > 0 &&
    currentAmount === amount &&
    current?.is_recurring === recurring &&
    currentRollover === rollover &&
    !resetRollover
  ) {
    return []
  }

  if (amount === 0) {
    // Clearing means no budget from the selected month onward. Keep a zero
    // row at the boundary to mask older recurring segments, and remove later
    // segments/overrides so they cannot revive the series.
    const operations: BudgetOperation[] = series
      .filter((budget) => budget.month > month && budget.id !== current?.id)
      .map((budget) => ({ type: 'delete', id: budget.id }))
    if (!current) return operations
    if (current.is_recurring) {
      operations.push({
        type: 'update',
        id: current.id,
        data: { amount: 0, effective_month: month, rollover: false },
      })
    } else {
      // Deleting this override would reveal the older recurring amount.
      operations.push({ type: 'update', id: current.id, data: { amount: 0, rollover: false } })
      const priorRecurring = series
        .filter((budget) => budget.is_recurring && budget.month <= month)
        .sort((left, right) => right.month.localeCompare(left.month))[0]
      if (priorRecurring) {
        // Stop the underlying series after this month as well; otherwise it
        // would reappear once the month-only zero override ends.
        operations.push({
          type: 'update',
          id: priorRecurring.id,
          data: { amount: 0, effective_month: nextMonth(month), rollover: false },
        })
      }
    }
    return operations
  }

  const resetPayload = resetRollover ? { reset_rollover: true } : {}

  if (recurring) {
    if (current?.is_recurring) {
      return [
        {
          type: 'update',
          id: current.id,
          data: { amount, effective_month: month, rollover, ...resetPayload },
        },
      ]
    }
    return [
      ...(current ? [{ type: 'delete', id: current.id } as const] : []),
      {
        type: 'create',
        data: { category_id: categoryId, amount, month, is_recurring: true, rollover, ...resetPayload },
      },
    ]
  }

  if (current && !current.is_recurring) {
    return [{ type: 'update', id: current.id, data: { amount, rollover, ...resetPayload } }]
  }
  if (current?.is_recurring) {
    // Keep this month's amount as a one-off, then stop the recurring series.
    return [
      {
        type: 'create',
        data: { category_id: categoryId, amount, month, is_recurring: false, rollover, ...resetPayload },
      },
      {
        type: 'update',
        id: current.id,
        data: { amount: 0, effective_month: nextMonth(month), rollover: false },
      },
    ]
  }
  return [
    {
      type: 'create',
      data: { category_id: categoryId, amount, month, is_recurring: false, rollover, ...resetPayload },
    },
  ]
}
