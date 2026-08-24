import { localDateString } from './date-utils'

export function creditCardCycleBoundaries(closeDay: number, reference = new Date()): { start: string; end: string } {
  const ref = new Date(reference)
  ref.setHours(0, 0, 0, 0)
  const clampDay = (year: number, month: number) => Math.min(closeDay, new Date(year, month + 1, 0).getDate())
  const thisMonthClose = new Date(ref.getFullYear(), ref.getMonth(), clampDay(ref.getFullYear(), ref.getMonth()))
  const nextClose = thisMonthClose > ref
    ? thisMonthClose
    : new Date(ref.getFullYear(), ref.getMonth() + 1, clampDay(ref.getFullYear(), ref.getMonth() + 1))
  const end = new Date(nextClose)
  end.setDate(end.getDate() - 1)
  const start = new Date(nextClose.getFullYear(), nextClose.getMonth() - 1, clampDay(nextClose.getFullYear(), nextClose.getMonth() - 1))
  return { start: localDateString(start), end: localDateString(end) }
}
