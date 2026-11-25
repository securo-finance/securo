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
 * Combines member asset valuations into wallet totals at their shared date set.
 * Carries each asset's latest known value forward, excluding dates before its
 * purchase or after its sale. Adds the day after each sale to reflect removal.
 * Omits a total if any asset held on that date lacks a known valuation.
 *
 * @param {Array<{
 *   purchase_date: string | null;
 *   sell_date: string | null;
 *   points: AmountPoint[];
 * }>} members - Asset valuation series with optional purchase and sale dates.
 * @param {string} from - Inclusive lower date bound in YYYY-MM-DD format.
 * @param {string} to - Inclusive upper date bound in YYYY-MM-DD format.
 * @returns {AmountPoint[]} Chronological wallet totals calculated using integer cents.
 */
export function combineWalletHistories(
  members: { purchase_date: string | null; sell_date: string | null; points: AmountPoint[] }[],
  from: string,
  to: string,
): AmountPoint[] {
  const dates = new Set(members.flatMap(member => member.points.map(p => p.date)))
  for (const member of members) {
    if (member.sell_date) {
      const day = new Date(member.sell_date + 'T00:00:00Z')
      day.setUTCDate(day.getUTCDate() + 1)
      dates.add(day.toISOString().slice(0, 10))
    }
  }
  // Build wallet totals in chronological order within the requested date range.
  // For each date, sum the latest known valuation of every asset held that day.
  // Skip assets before purchase or after sale. If any held asset lacks a valuation,
  // omit that date entirely: flatMap's empty array adds no point to the result.
  // Sum in integer cents, then convert the total back to currency units.
  return [...dates]
    .sort()
    .filter(date => date >= from && date <= to)
    .flatMap(date => {
      let total = 0
      for (const member of members) {
        if (
          (member.purchase_date && date < member.purchase_date) ||
          (member.sell_date && date > member.sell_date)
        ) {
          continue
        }
        const point = member.points
          .filter(p => p.date <= date)
          .sort((a, b) => b.date.localeCompare(a.date))[0]
        // Missing valuations are unknown, never zero. Omit incomplete totals.
        if (!point) {
          return []
        }
        total += cents(point.amount)
      }
      return [{ date, amount: total / 100 }]
    })
}
