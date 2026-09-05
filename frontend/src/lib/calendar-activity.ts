import type { TransactionCalendarDay, TransactionCalendarItem } from '../types'

/** Persisted forecast rows remain editable; only virtual occurrences lack an id. */
export function isCalendarItemInteractive(item: TransactionCalendarItem): boolean {
  return item.id != null
}

export interface CalendarDayActivity {
  date: string
  actualIncome: number
  actualExpense: number
  actualNet: number
  projectedIncome: number
  projectedExpense: number
  projectedNet: number
  hasActual: boolean
  hasProjected: boolean
}

export function dayActivity(day: TransactionCalendarDay): CalendarDayActivity {
  const actualIncome = day.actual_income ?? 0
  const actualExpense = day.actual_expense ?? 0
  const projectedIncome = day.projected_income ?? 0
  const projectedExpense = day.projected_expense ?? 0
  return {
    date: day.date,
    actualIncome,
    actualExpense,
    actualNet: actualIncome - actualExpense,
    projectedIncome,
    projectedExpense,
    projectedNet: projectedIncome - projectedExpense,
    hasActual: actualIncome > 0 || actualExpense > 0,
    hasProjected: projectedIncome > 0 || projectedExpense > 0,
  }
}

export interface ActivityChartData {
  days: CalendarDayActivity[]
  // Chart scale: projected amounts stack on top of the actual bar in each
  // direction, so the axis must fit the combined height.
  maxUp: number
  maxDown: number
  hasActivity: boolean
}

export function activityChartData(days: TransactionCalendarDay[]): ActivityChartData {
  const mapped = days.map(dayActivity)
  let maxUp = 0
  let maxDown = 0
  for (const day of mapped) {
    maxUp = Math.max(maxUp, day.actualIncome + day.projectedIncome)
    maxDown = Math.max(maxDown, day.actualExpense + day.projectedExpense)
  }
  return {
    days: mapped,
    maxUp,
    maxDown,
    hasActivity: maxUp > 0 || maxDown > 0,
  }
}
