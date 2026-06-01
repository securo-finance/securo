/**
 * Month navigation helpers shared by the dashboard and the transactions page.
 *
 * Months are represented as `"YYYY-MM"` strings. Day ranges are emitted as
 * `"YYYY-MM-DD"` strings WITHOUT a time component, matching the date-range
 * filter and the `?from`/`?to` URL params (which are timezone-naive).
 */

/** Current month as `"YYYY-MM"` (browser local time). */
export function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

/** Shift a `"YYYY-MM"` month by `delta` months (handles year overflow). */
export function shiftMonth(yearMonth: string, delta: number): string {
  const [y, m] = yearMonth.split('-').map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

/** Last day-of-month number for a `"YYYY-MM"` month (28–31, leap-year aware). */
export function monthLastDay(yearMonth: string): number {
  const [y, m] = yearMonth.split('-').map(Number)
  return new Date(y, m, 0).getDate()
}

/** First/last day of a `"YYYY-MM"` month as `"YYYY-MM-DD"` strings. */
export function monthRange(yearMonth: string): { from: string; to: string } {
  return {
    from: `${yearMonth}-01`,
    to: `${yearMonth}-${String(monthLastDay(yearMonth)).padStart(2, '0')}`,
  }
}

/** Localized label for a `"YYYY-MM"` month, e.g. "Maio de 2026" / "May 2026". */
export function monthLabel(yearMonth: string, locale = 'pt-BR'): string {
  const [y, m] = yearMonth.split('-').map(Number)
  return new Date(y, m - 1, 2).toLocaleDateString(locale, { month: 'long', year: 'numeric' })
}

/**
 * If a `from`/`to` pair exactly spans one full calendar month, return that
 * month as `"YYYY-MM"`. Otherwise return null (custom range / no filter).
 * Lets the stepper reflect the active date-range filter as a single month.
 */
export function monthFromRange(from: string | null | undefined, to: string | null | undefined): string | null {
  if (!from || !to) return null
  const ym = from.slice(0, 7)
  const expected = monthRange(ym)
  return from === expected.from && to === expected.to ? ym : null
}
