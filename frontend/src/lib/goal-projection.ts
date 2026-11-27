import {
  addDays,
  addYears,
  differenceInCalendarMonths,
  format,
  max,
  parseISO,
  subMonths,
  subYears,
} from 'date-fns'
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
 * Samples the straight-line pace weekly, ending exactly on the goal deadline.
 * This is a required-pace guide, not a prediction of monthly contributions.
 * Undated goals have no projection. Reached or overdue dated goals retain only
 * today's amount rather than drawing a backward or declining projection.
 *
 * @param {Goal} goal - Goal supplying its current amount, target, and deadline.
 * @param {string} today - Current workspace date in YYYY-MM-DD format.
 * @returns {{ projection: AmountPoint[] }} Weekly projection points, including today and the exact deadline.
 */
export function projectGoal(goal: Goal, today: string) {
  const projection: AmountPoint[] = []
  if (!goal.target_date) {
    return { projection }
  }

  const current = cents(goal.current_amount)
  const target = cents(goal.target_amount)
  projection.push({ date: today, amount: current / 100 })

  // setup intermediate points for every week after today.
  if (current < target && goal.target_date > today) {
    const start = Date.parse(today + 'T00:00:00Z')
    const end = Date.parse(goal.target_date + 'T00:00:00Z')
    const week = 7 * 86_400_000
    for (let time = start + week; time < end; time += week) {
      const fraction = (time - start) / (end - start)
      projection.push({
        date: new Date(time).toISOString().slice(0, 10),
        amount: Math.round(current + (target - current) * fraction) / 100,
      })
    }
    // push the final projection point at the target and gaol date.
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
 * Counts the calendar months a history window covers, capped at the ten years
 * the value-trend API accepts and never below one.
 *
 * @param {string} from - Inclusive window start in YYYY-MM-DD format.
 * @param {string} to - Inclusive window end in YYYY-MM-DD format.
 * @returns {number} Months to request from the value-trend endpoint.
 */
export function historyMonths(from: string, to: string): number {
  const months = differenceInCalendarMonths(parseISO(to), parseISO(from)) + 1
  return Math.min(120, Math.max(1, months))
}

/**
 * Carries the last valuation before the window forward to its start, so an
 * asset valued at long intervals still draws a line inside the window instead
 * of being reported as having no history. The window start is left untouched
 * when a valuation already falls on it.
 *
 * @param {AmountPoint[]} points - Valuations returned by the asset API.
 * @param {string} from - Inclusive window start in YYYY-MM-DD format.
 * @returns {AmountPoint[]} The original points plus the carried-forward value.
 */
export function carryForward(points: AmountPoint[], from: string): AmountPoint[] {
  if (points.some(point => point.date === from)) {
    return points
  }
  const lastBefore = points
    .filter(
      point =>
        /^\d{4}-\d{2}-\d{2}$/.test(point.date) &&
        point.date < from &&
        Number.isFinite(Number(point.amount)),
    )
    .reduce<AmountPoint | null>(
      (latest, point) => (latest === null || point.date >= latest.date ? point : latest),
      null,
    )
  if (lastBefore === null) {
    return points
  }
  return [...points, { date: from, amount: cents(lastBefore.amount) / 100 }]
}

/**
 * Builds month-anchored x-axis ticks, keeping the range ends and thinning long
 * ranges. Short windows therefore never repeat the same month label.
 *
 * @param {string} from - Inclusive range start in YYYY-MM-DD format.
 * @param {string} to - Inclusive range end in YYYY-MM-DD format.
 * @param {number} [max=6] - Highest number of ticks to keep.
 * @returns {number[]} Tick timestamps in UTC milliseconds, ascending.
 */
export function axisTicks(from: string, to: string, max = 6): number[] {
  const start = Date.parse(from + 'T00:00:00Z')
  const end = Date.parse(to + 'T00:00:00Z')
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return [start]
  }

  const times = new Set<number>([start, end])
  const first = new Date(start)
  let cursor = Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 1)
  while (cursor < end) {
    times.add(cursor)
    const month = new Date(cursor)
    cursor = Date.UTC(month.getUTCFullYear(), month.getUTCMonth() + 1, 1)
  }

  // A month start in the end tick's own month would label itself and the end
  // with the same month, so the end alone carries that month.
  const endDate = new Date(end)
  const ascending = [...times]
    .filter(time => {
      if (time === start || time === end) {
        return true
      }
      const date = new Date(time)
      return (
        date.getUTCFullYear() !== endDate.getUTCFullYear() ||
        date.getUTCMonth() !== endDate.getUTCMonth()
      )
    })
    .sort((a, b) => a - b)
  const step = Math.max(1, Math.ceil((ascending.length - 1) / (Math.max(2, max) - 1)))
  const ticks = ascending.filter((_, index) => index % step === 0)
  const last = ascending.at(-1)
  if (last !== undefined && ticks.at(-1) !== last) {
    ticks[ticks.length - 1] = last
  }
  return ticks
}

/**
 * Places the target label so it covers neither the x-axis labels nor the
 * dashed target line. A reference line has no height, so Recharts reads
 * "insideTopRight" on the floor line as one line *below* it, over the tick
 * labels; the label goes above the line ("insideBottomRight") there instead.
 *
 * @param {number} target - Goal target amount in major currency units.
 * @param {number} floor - Lower bound of the vertical axis.
 * @returns {'insideTopLeft' | 'insideBottomRight'} Recharts label position.
 */
export function targetLabelSide(
  target: number,
  floor: number,
): 'insideTopLeft' | 'insideBottomRight' {
  return target <= floor ? 'insideBottomRight' : 'insideTopLeft'
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
