import { addDays, addYears, format, max, parseISO, subMonths, subYears } from 'date-fns'
import type { Goal } from '@/types'

export interface AmountPoint {
  date: string
  amount: number
}

/**
 * Converts a monetary amount to integer cents for two-decimal arithmetic.
 *
 * @param {number | string} value - Amount in major currency units, as a number or numeric string.
 * @returns {number} The amount rounded to the nearest cent.
 * @throws {Error} If the amount is nonfinite or exceeds the safe integer range.
 */
export function cents(value: number | string): number {
  const parsed = Number(value)
  const result = Math.round((parsed + Math.sign(parsed) * Number.EPSILON) * 100)
  if (!Number.isFinite(parsed) || !Number.isSafeInteger(result)) {
    throw new Error('Invalid money amount')
  }
  return result
}

/**
 * Returns the first day of the month at the requested offset from an ISO date.
 * Uses UTC calendar arithmetic so the browser timezone cannot shift the day.
 *
 * @param {string} today - Reference date in YYYY-MM-DD format.
 * @param {number} [months=1] - Calendar month offset; defaults to one and may be negative.
 * @returns {string} The first of the destination month in YYYY-MM-DD format.
 */
export function firstOfMonthAfter(today: string, months = 1): string {
  const date = new Date(today + 'T00:00:00Z')
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + months, 1))
    .toISOString()
    .slice(0, 10)
}

/**
 * Adds one calendar month to an ISO date, clamping to the destination month end.
 * For example, January 31 becomes February 28 in a nonleap year.
 *
 * @param {string} day - Date in YYYY-MM-DD format.
 * @returns {string} The adjusted date in YYYY-MM-DD format, calculated in UTC.
 */
export function calendarMonthAfter(day: string): string {
  const date = new Date(day + 'T00:00:00Z')
  const lastDay = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 2, 0)).getUTCDate()
  return new Date(
    Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, Math.min(date.getUTCDate(), lastDay)),
  )
    .toISOString()
    .slice(0, 10)
}

/**
 * Builds the endpoints of the straight-line pace needed to reach a dated goal.
 * This is a required-pace guide, not a prediction of monthly contributions.
 * Undated goals have no projection. Reached or overdue dated goals retain only
 * today's amount rather than drawing a backward or declining projection.
 *
 * @param {Goal} goal - Goal supplying its current amount, target, and deadline.
 * @param {string} today - Current workspace date in YYYY-MM-DD format.
 * @returns {{ projection: AmountPoint[] }} Zero, one, or two projection points.
 */
export function projectGoal(goal: Goal, today: string) {
  const projection: AmountPoint[] = []
  if (!goal.target_date) {
    return { projection }
  }

  const current = cents(goal.current_amount)
  const target = cents(goal.target_amount)
  projection.push({ date: today, amount: current / 100 })

  if (current < target && goal.target_date > today) {
    projection.push({ date: goal.target_date, amount: target / 100 })
  }

  return { projection }
}

/**
 * Filters history to an inclusive date range and returns it in ascending order.
 * Requires YYYY-MM-DD date labels and finite amounts, rounds amounts to cents,
 * and keeps the last qualifying point when multiple points share a date.
 * This checks date formatting, not calendar validity.
 *
 * @param {AmountPoint[]} points - Historical amounts in major currency units.
 * @param {string} from - Inclusive lower date bound in YYYY-MM-DD format.
 * @param {string} to - Inclusive upper date bound in YYYY-MM-DD format.
 * @returns {AmountPoint[]} Sorted, deduplicated points within the requested range.
 */
export function cleanHistory(points: AmountPoint[], from: string, to: string): AmountPoint[] {
  const byDate = new Map<string, number>()
  for (const point of points) {
    if (
      /^\d{4}-\d{2}-\d{2}$/.test(point.date) &&
      point.date >= from &&
      point.date <= to &&
      Number.isFinite(Number(point.amount))
    ) {
      byDate.set(point.date, cents(point.amount) / 100)
    }
  }
  return [...byDate]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, amount]) => ({ date, amount }))
}

/**
 * Starts history one month before creation, capped at the API's ten-year limit.
 *
 * @param {Goal} goal - Goal with its UTC creation timestamp.
 * @param {string} today - Workspace date in YYYY-MM-DD format.
 * @returns {string} Inclusive start date shared by history requests and the x-axis.
 */
export function goalHistoryStart(goal: Goal, today: string): string {
  const end = parseISO(today)
  const tenYearsAgo = subYears(end, 10)
  // On February 29, use March 1 if clamping back ten years loses a day.
  const limit = addYears(tenYearsAgo, 10) < end ? addDays(tenYearsAgo, 1) : tenYearsAgo
  const beforeCreation = subMonths(parseISO(goal.created_at.slice(0, 10)), 1)
  return format(max([beforeCreation, limit]), 'yyyy-MM-dd')
}
