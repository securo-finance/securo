/**
 * Period resolution — TypeScript twin of `backend/app/services/period_service.py`.
 *
 * Mirrors the backend's date math so the UI can preview what range the API
 * will resolve to (used in chart axes, query keys, and disabled-state guards
 * before round-tripping). Keep the two in lockstep — both have unit tests
 * covering the same edge cases.
 *
 * Civil calendar (locked 2026-06-29):
 *   - Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec
 *   - S1=Jan-Jun, S2=Jul-Dec
 *   - Weekly: weekStart=0 → Sun..Sat (PT-BR), weekStart=1 → Mon..Sun (EN/ISO)
 */

export type PeriodMode =
  | 'daily'
  | 'weekly'
  | 'monthly'
  | 'quarterly'
  | 'half_yearly'
  | 'yearly'
  | 'custom'

export interface PeriodValue {
  mode: PeriodMode
  /** Anchor date as YYYY-MM-DD. The "what period am I looking at" cursor. */
  anchor: string
}

export interface PeriodRange {
  start: string
  end: string
}

// ----------------------------------------------------------------- date utils

function toIso(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function parseIso(s: string): Date {
  // Anchor is always YYYY-MM-DD. Build as local-time Date to avoid TZ drift.
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function lastDayOfMonth(year: number, month1Based: number): number {
  return new Date(year, month1Based, 0).getDate()
}

function startOfWeek(d: Date, weekStart: number): Date {
  // weekStart: 0=Sun, 1=Mon, ..., 6=Sat
  const jsDay = d.getDay() // 0=Sun..6=Sat
  const offset = (jsDay - weekStart + 7) % 7
  const out = new Date(d)
  out.setDate(out.getDate() - offset)
  return out
}

// ------------------------------------------------------------------ resolve

export function resolvePeriod(
  mode: PeriodMode,
  anchorIso: string,
  weekStart = 0,
): PeriodRange {
  if (mode === 'custom') {
    throw new Error('custom mode requires explicit from/to; resolvePeriod does not handle it')
  }
  const anchor = parseIso(anchorIso)

  if (mode === 'daily') {
    return { start: anchorIso, end: anchorIso }
  }
  if (mode === 'weekly') {
    const start = startOfWeek(anchor, weekStart)
    const end = new Date(start)
    end.setDate(end.getDate() + 6)
    return { start: toIso(start), end: toIso(end) }
  }
  if (mode === 'monthly') {
    const y = anchor.getFullYear()
    const m = anchor.getMonth() + 1
    return {
      start: toIso(new Date(y, m - 1, 1)),
      end: toIso(new Date(y, m - 1, lastDayOfMonth(y, m))),
    }
  }
  if (mode === 'quarterly') {
    const y = anchor.getFullYear()
    const q = Math.floor((anchor.getMonth()) / 3)
    const firstMonth = q * 3 + 1
    const lastMonth = firstMonth + 2
    return {
      start: toIso(new Date(y, firstMonth - 1, 1)),
      end: toIso(new Date(y, lastMonth - 1, lastDayOfMonth(y, lastMonth))),
    }
  }
  if (mode === 'half_yearly') {
    const y = anchor.getFullYear()
    const sem = anchor.getMonth() < 6 ? 0 : 1
    const firstMonth = sem === 0 ? 1 : 7
    const lastMonth = firstMonth + 5
    return {
      start: toIso(new Date(y, firstMonth - 1, 1)),
      end: toIso(new Date(y, lastMonth - 1, lastDayOfMonth(y, lastMonth))),
    }
  }
  // yearly
  return {
    start: toIso(new Date(anchor.getFullYear(), 0, 1)),
    end: toIso(new Date(anchor.getFullYear(), 11, 31)),
  }
}

// ------------------------------------------------------------------ shift

export function shiftAnchor(
  mode: PeriodMode,
  anchorIso: string,
  direction: 1 | -1,
): string {
  if (mode === 'custom') {
    throw new Error('cannot shift anchor for custom mode')
  }
  const anchor = parseIso(anchorIso)

  if (mode === 'daily') {
    const d = new Date(anchor)
    d.setDate(d.getDate() + direction)
    return toIso(d)
  }
  if (mode === 'weekly') {
    const d = new Date(anchor)
    d.setDate(d.getDate() + 7 * direction)
    return toIso(d)
  }

  // month-based modes (monthly, quarterly, half_yearly): step via 0-based month index.
  let monthStep: number
  if (mode === 'monthly') monthStep = direction
  else if (mode === 'quarterly') monthStep = 3 * direction
  else if (mode === 'half_yearly') monthStep = 6 * direction
  else monthStep = 0 // yearly handled below

  if (mode !== 'yearly') {
    const mIndex = anchor.getMonth() + monthStep
    const targetY = anchor.getFullYear() + Math.floor(mIndex / 12)
    const targetM = ((mIndex % 12) + 12) % 12
    const targetD = Math.min(
      anchor.getDate(),
      lastDayOfMonth(targetY, targetM + 1),
    )
    return toIso(new Date(targetY, targetM, targetD))
  }

  // yearly
  const targetY = anchor.getFullYear() + direction
  const targetD = Math.min(
    anchor.getDate(),
    lastDayOfMonth(targetY, anchor.getMonth() + 1),
  )
  return toIso(new Date(targetY, anchor.getMonth(), targetD))
}

// -------------------------------------------------- locale-derived week_start

/**
 * Resolve the calendar week-start convention from a BCP-47 locale string.
 * Uses the built-in Intl.Locale.weekInfo API (Chrome 99+, Firefox 99+,
 * Safari 15.4+). Falls back to a small static table for older runtimes or
 * missing data, and finally to Monday for anything unknown.
 *
 * Returns the `weekStart` value expected by resolvePeriod/shiftAnchor:
 *   0 = Sunday (PT-BR, en-US, es, ja, ko, zh, he)
 *   1 = Monday (en-GB, fr, de, it, nl, pl, pt-PT, ru, uk, sv, da, no, fi, ...)
 */
const SUNDAY_LOCALES = new Set([
  'pt-BR', 'en-US', 'es', 'ja', 'ko', 'zh', 'he',
])
const MONDAY_DEFAULT = 1

export function weekStartFromLocale(locale: string): 0 | 1 {
  // Try the spec-compliant API first.
  try {
    const intl = new Intl.Locale(locale)
    // weekInfo is on Intl.Locale in modern browsers/Node 22+. Some TS lib
    // versions don't declare it; cast through unknown.
    const weekInfo = (intl as unknown as { weekInfo?: { firstDay?: number } }).weekInfo
    if (weekInfo?.firstDay === 7) return 0
    if (weekInfo?.firstDay === 1) return 1
  } catch {
    // Locale string invalid — fall through to static table.
  }

  // Static-table fallback. Match on language OR full tag.
  const lower = locale.toLowerCase()
  const base = lower.split('-')[0]
  if (SUNDAY_LOCALES.has(locale) || SUNDAY_LOCALES.has(base)) return 0
  return MONDAY_DEFAULT
}