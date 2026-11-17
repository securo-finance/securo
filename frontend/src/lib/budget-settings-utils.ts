import type { Budget } from '@/types'
import type { BudgetVsActual } from '@/types'
import { parseAmountInput } from '@/lib/format'

type DecimalAmount = number | string
type BudgetVsActualApiRow = Omit<BudgetVsActual, 'budget_amount' | 'actual_amount' | 'projected_amount' | 'prev_month_amount' | 'projected_prev_month_amount' | 'percentage_used'> & {
  budget_amount: DecimalAmount | null
  actual_amount: DecimalAmount
  projected_amount: DecimalAmount
  prev_month_amount: DecimalAmount
  projected_prev_month_amount: DecimalAmount
  percentage_used: DecimalAmount | null
}

export function normalizeBudgetComparisonRows(rows: BudgetVsActualApiRow[]): BudgetVsActual[] {
  return rows.map((row) => ({
    ...row,
    budget_amount: row.budget_amount === null ? null : Number(row.budget_amount),
    actual_amount: Number(row.actual_amount),
    projected_amount: Number(row.projected_amount),
    prev_month_amount: Number(row.prev_month_amount),
    projected_prev_month_amount: Number(row.projected_prev_month_amount),
    percentage_used: row.percentage_used === null ? null : Number(row.percentage_used),
  }))
}

export type BudgetOperation =
  | { type: 'create'; data: { category_id: string; amount: number; month: string; is_recurring: boolean } }
  | { type: 'update'; id: string; data: { amount: number; effective_month?: string } }
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
  current,
  series = current ? [current] : [],
  month,
}: {
  categoryId: string
  amount: number
  recurring: boolean
  current: Budget | undefined
  series?: Budget[]
  month: string
}): BudgetOperation[] {
  // The API sends decimals as strings.
  const currentAmount = Number(current?.amount ?? 0)
  if (amount > 0 && currentAmount === amount && current?.is_recurring === recurring) return []

  if (amount === 0) {
    // Clearing means no budget from the selected month onward. Keep a zero
    // row at the boundary to mask older recurring segments, and remove later
    // segments/overrides so they cannot revive the series.
    const operations: BudgetOperation[] = series
      .filter((budget) => budget.month > month && budget.id !== current?.id)
      .map((budget) => ({ type: 'delete', id: budget.id }))
    if (!current) return operations
    if (current.is_recurring) {
      operations.push({ type: 'update', id: current.id, data: { amount: 0, effective_month: month } })
    } else {
      // Deleting this override would reveal the older recurring amount.
      operations.push({ type: 'update', id: current.id, data: { amount: 0 } })
      const priorRecurring = series
        .filter((budget) => budget.is_recurring && budget.month <= month)
        .sort((left, right) => right.month.localeCompare(left.month))[0]
      if (priorRecurring) {
        // Stop the underlying series after this month as well; otherwise it
        // would reappear once the month-only zero override ends.
        operations.push({ type: 'update', id: priorRecurring.id, data: { amount: 0, effective_month: nextMonth(month) } })
      }
    }
    return operations
  }

  if (recurring) {
    if (current?.is_recurring) {
      return [{ type: 'update', id: current.id, data: { amount, effective_month: month } }]
    }
    return [
      ...(current ? [{ type: 'delete', id: current.id } as const] : []),
      { type: 'create', data: { category_id: categoryId, amount, month, is_recurring: true } },
    ]
  }

  if (current && !current.is_recurring) {
    return [{ type: 'update', id: current.id, data: { amount } }]
  }
  if (current?.is_recurring && currentAmount === amount) {
    // Keep this month's amount as a one-off, then stop the recurring series.
    return [
      { type: 'create', data: { category_id: categoryId, amount, month, is_recurring: false } },
      { type: 'update', id: current.id, data: { amount: 0, effective_month: nextMonth(month) } },
    ]
  }
  if (current?.is_recurring) {
    return [
      { type: 'create', data: { category_id: categoryId, amount, month, is_recurring: false } },
      { type: 'update', id: current.id, data: { amount: 0, effective_month: nextMonth(month) } },
    ]
  }
  return [{ type: 'create', data: { category_id: categoryId, amount, month, is_recurring: false } }]
}
