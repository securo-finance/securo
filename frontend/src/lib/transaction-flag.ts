import { localToday } from '@/lib/invoice-schedule-utils'
import type { FlagDue, Transaction } from '@/types'

/** Days after today that still count as "due soon", matching the server. */
export const FLAG_DUE_SOON_DAYS = 7

export type FlagState = 'none' | 'flagged' | 'soon' | 'overdue'

/** The flag filter as kept in the URL: `?flagged=1|overdue|soon`. */
export type FlagFilter = '' | 'all' | FlagDue

export function parseFlagFilter(value: string | null): FlagFilter {
  if (value === 'overdue' || value === 'soon') return value
  return value === '1' ? 'all' : ''
}

export function flagFilterToParam(filter: FlagFilter): string {
  return filter === 'all' ? '1' : filter
}

export function addDays(iso: string, days: number): string {
  const date = new Date(iso + 'T00:00:00')
  date.setDate(date.getDate() + days)
  return localToday(date)
}

export function addMonths(iso: string, months: number): string {
  const date = new Date(iso + 'T00:00:00')
  const day = date.getDate()
  date.setDate(1)
  date.setMonth(date.getMonth() + months)
  // Clamp to the month's last day: 31 Jan plus one month is 28/29 Feb.
  const lastDay = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate()
  date.setDate(Math.min(day, lastDay))
  return localToday(date)
}

export function flagState(
  tx: Pick<Transaction, 'is_flagged' | 'flag_due_date'>,
  today: string = localToday(),
): FlagState {
  if (!tx.is_flagged) return 'none'
  const due = tx.flag_due_date
  if (!due) return 'flagged'
  if (due < today) return 'overdue'
  if (due <= addDays(today, FLAG_DUE_SOON_DAYS)) return 'soon'
  return 'flagged'
}

export interface FlagDuePreset {
  key: 'tomorrow' | 'nextWeek' | 'nextMonth'
  date: string
}

export function flagDuePresets(today: string = localToday()): FlagDuePreset[] {
  return [
    { key: 'tomorrow', date: addDays(today, 1) },
    { key: 'nextWeek', date: addDays(today, 7) },
    { key: 'nextMonth', date: addMonths(today, 1) },
  ]
}
