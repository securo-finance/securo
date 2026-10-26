/**
 * Period window math for the Reports date range selector.
 *
 * Presets (6M, YTD, 1Y, ...) and the custom range are normally anchored to
 * today: the API derives the start from `months` / `period` / `days` (or the
 * page sends the picked custom dates). Period navigation (issue #519) steps
 * the same window back by its own length: the window keeps its shape and every
 * date inside it moves back together. Shifted windows are sent to the API as
 * explicit `start_date` / `end_date`.
 */

import { monthRange, shiftMonth } from './month-utils'

/** Explicit report window as timezone-naive `YYYY-MM-DD` strings. */
export interface PeriodWindow {
  from: string
  to: string
}

/** The five shapes a range preset can have. */
export type PeriodPreset =
  | { kind: 'month'; anchorDelta: number } // This Month (0) / Last Month (-1)
  | { kind: 'ytd'; fyStartMonth?: number } // YTD, start of the financial year (1 = January)
  | { kind: 'rolling'; months: number } // 6M / 1Y / 2Y ...
  | { kind: 'days'; days: number } // 30D rolling window
  | { kind: 'custom' } // user-picked from/to

function parseIso(iso: string): { y: number; m: number; d: number } {
  const [y, m, d] = iso.split('-').map(Number)
  return { y, m, d }
}

function iso(y: number, m: number, d: number): string {
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

/** Shift a date by whole days (calendar math, DST safe). */
export function addDaysIso(isoDate: string, delta: number): string {
  const { y, m, d } = parseIso(isoDate)
  const t = new Date(y, m - 1, d + delta)
  return iso(t.getFullYear(), t.getMonth() + 1, t.getDate())
}

/** Shift a date by whole months, clamping the day to the target month's end. */
export function addMonthsIso(isoDate: string, delta: number): string {
  const { y, m, d } = parseIso(isoDate)
  const target = new Date(y, m - 1 + delta, 1)
  const lastDay = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate()
  return iso(target.getFullYear(), target.getMonth() + 1, Math.min(d, lastDay))
}

/** Whole days from `from` to `to` (negative when `to` is earlier). */
export function daysBetween(fromIso: string, toIso: string): number {
  const f = parseIso(fromIso)
  const t = parseIso(toIso)
  return Math.round((Date.UTC(t.y, t.m - 1, t.d) - Date.UTC(f.y, f.m - 1, f.d)) / 86_400_000)
}

/**
 * Start of the rolling window for a `months` preset. Mirrors the backend:
 * first day of the current month, back by `months * 30` days, snapped to the
 * first day of that month (so 6M today means "since the 1st, ~6 months back").
 */
export function rollingStart(today: string, months: number): string {
  const { y, m } = parseIso(today)
  return addDaysIso(iso(y, m, 1), -(months * 30)).slice(0, 8) + '01'
}

/**
 * Window width in months: ceil of the span in days / 30, clamped to 1..24.
 * Same rule the page uses when it sends a custom range, so shifted windows
 * keep the `months` parameter the API expects.
 */
export function windowMonths(window: PeriodWindow): number {
  return Math.max(1, Math.min(24, Math.ceil(daysBetween(window.from, window.to) / 30)))
}

/**
 * Explicit window for a preset at a period offset.
 *
 * - Month presets (This Month / Last Month) always return dates: the API has
 *   no equivalent preset. `This Month` at offset 0 ends on `today`; every
 *   other combination is a full calendar month.
 * - `ytd` at offset 0 returns null (the API's `period=ytd` path); other
 *   offsets span whole previous financial years.
 * - `rolling` / `days` at offset 0 return null (the API's `months` / `days`
 *   path); negative offsets step back by whole windows of the same number
 *   of calendar months, each ending the day before the next one starts.
 *
 * `offset` counts periods of the preset (months, financial years, the
 * rolling length or the custom span), not calendar months.
 * - `custom` at offset 0 returns null (the page already sends the picked
 *   dates); negative offsets step the picked range back by its own length in
 *   months.
 */
export function periodWindow(
  preset: PeriodPreset,
  today: string,
  offset: number,
  customRange?: PeriodWindow,
): PeriodWindow | null {
  switch (preset.kind) {
    case 'month': {
      const anchor = shiftMonth(today.slice(0, 7), preset.anchorDelta + offset)
      const full = monthRange(anchor)
      if (offset === 0 && preset.anchorDelta === 0) {
        return { from: full.from, to: today }
      }
      return full
    }
    case 'ytd': {
      if (offset === 0) return null
      const fy = preset.fyStartMonth ?? 1
      const t = parseIso(today)
      const startYear = t.y - (t.m < fy ? 1 : 0) + offset
      const from = iso(startYear, fy, 1)
      return { from, to: addDaysIso(addMonthsIso(from, 12), -1) }
    }
    case 'rolling': {
      if (offset === 0) return null
      // The current window runs from the 1st of its first month to today, so
      // it spans whole calendar months. Earlier windows are that many full
      // months each and end the day before the next one starts: no overlap.
      const start = rollingStart(today, preset.months)
      const s = parseIso(start)
      const t = parseIso(today)
      const span = (t.y - s.y) * 12 + (t.m - s.m) + 1
      return {
        from: addMonthsIso(start, span * offset),
        to: addDaysIso(addMonthsIso(start, span * (offset + 1)), -1),
      }
    }
    case 'days': {
      if (offset === 0) return null
      const step = preset.days * offset
      return {
        from: addDaysIso(today, -(preset.days - 1) + step),
        to: addDaysIso(today, step),
      }
    }
    case 'custom': {
      if (offset === 0 || !customRange) return null
      const spanMonths = Math.max(1, Math.ceil(daysBetween(customRange.from, customRange.to) / 30))
      return {
        from: addMonthsIso(customRange.from, spanMonths * offset),
        to: addMonthsIso(customRange.to, spanMonths * offset),
      }
    }
  }
}
