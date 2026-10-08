/**
 * Per-page memory of the last date range picked (issue #1053), so leaving
 * Transactions or Reports and coming back shows the range the user was on
 * instead of the page's default.
 *
 * Only a convenience: a range in the URL (bookmark, drill-down link) always
 * wins, and storage that is blocked or holds something unreadable falls back
 * to the default. The current month is remembered as "the current month",
 * not as its dates, so a range saved in March doesn't pin the page to March
 * once April starts.
 */
import { currentMonth, monthFromRange, monthRange } from '@/lib/month-utils'

const PREFIX = 'securo.dateRange.'

export type RememberedPage = 'transactions' | 'reports'

function read(page: RememberedPage): unknown {
  try {
    const raw = localStorage.getItem(PREFIX + page)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function write(page: RememberedPage, value: unknown): void {
  try {
    localStorage.setItem(PREFIX + page, JSON.stringify(value))
  } catch {
    // Private mode or quota: remembering is best-effort.
  }
}

/** '' (open end) or a real calendar date as YYYY-MM-DD — not "2026-02-30". */
function isDate(v: unknown): v is string {
  if (v === '') return true
  if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return false
  const [y, m, d] = v.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1, d))
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d
}

/** A from/to range ('' on either side means open-ended). */
export function loadRange(page: RememberedPage): { from: string; to: string } | null {
  const v = read(page) as { kind?: string; from?: unknown; to?: unknown } | null
  if (v?.kind === 'current-month') return monthRange(currentMonth())
  if (v?.kind === 'range' && isDate(v.from) && isDate(v.to)) return { from: v.from, to: v.to }
  return null
}

export function saveRange(page: RememberedPage, from: string, to: string): void {
  write(page, monthFromRange(from, to) === currentMonth() ? { kind: 'current-month' } : { kind: 'range', from, to })
}

/** Reports, per tab: a preset key (relative, e.g. "1y") or custom dates, plus the interval. */
export interface ReportsRange {
  rangeKey: string
  from: string
  to: string
  interval: string
}

/** Stored per-tab entries; anything that isn't an object (an older flat shape) is dropped. */
function readReportsTabs(): Record<string, unknown> {
  const v = read('reports')
  if (!v || typeof v !== 'object' || Array.isArray(v)) return {}
  return Object.fromEntries(
    Object.entries(v).filter(([, entry]) => entry !== null && typeof entry === 'object'),
  )
}

export function loadReportsRange(tab: string): ReportsRange | null {
  const v = readReportsTabs()[tab] as Partial<Record<keyof ReportsRange, unknown>> | undefined
  if (!v || typeof v.rangeKey !== 'string' || typeof v.interval !== 'string') return null
  if (!isDate(v.from) || !isDate(v.to)) return null
  return { rangeKey: v.rangeKey, from: v.from, to: v.to, interval: v.interval }
}

export function saveReportsRange(tab: string, range: ReportsRange): void {
  write('reports', { ...readReportsTabs(), [tab]: range })
}
