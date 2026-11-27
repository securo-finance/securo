import { format } from 'date-fns'

export function localDateString(date = new Date()) {
  return format(date, 'yyyy-MM-dd')
}

// Today as the server sees it. The server keeps each workspace's books in a
// timezone of its own, so "today" for a balance or a due date is that zone's
// day, which is not always the browser's. Without a zone (still loading, or a
// viewer with no workspace) the browser's day is the best guess available.
// Assembled from the date parts rather than from a locale's string form, so
// the result is yyyy-MM-dd on every Intl implementation.
export function todayInTimezone(timeZone?: string | null, now = new Date()) {
  if (!timeZone) return localDateString(now)
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(now)
    const part = (type: string) => parts.find((p) => p.type === type)?.value
    const year = part('year')
    const month = part('month')
    const day = part('day')
    if (!year || !month || !day) return localDateString(now)
    return `${year}-${month}-${day}`
  } catch {
    return localDateString(now)
  }
}

// Tick labels for a chart over yyyy-MM-dd points. Month and year are enough
// when every point sits in a month of its own; once two points share a month
// (daily values, a portfolio started this month) that label would repeat on
// the axis ("Nov 26, Nov 26, Nov 26"), so the day is shown, and the year only
// when the points cross one.
export function dateTickFormatter(dates: string[], locale: string) {
  const months = new Set(dates.map((d) => d.slice(0, 7)))
  const years = new Set(dates.map((d) => d.slice(0, 4)))
  const options: Intl.DateTimeFormatOptions =
    months.size === dates.length
      ? { month: 'short', year: '2-digit' }
      : years.size > 1
        ? { day: 'numeric', month: 'short', year: '2-digit' }
        : { day: 'numeric', month: 'short' }
  return (value: string) => new Date(value + 'T00:00:00').toLocaleDateString(locale, options)
}

// Short weekday names for a Sunday-start calendar header. The reference week is
// anchored in UTC, so it has to be formatted in UTC as well — otherwise a viewer
// behind UTC reads each instant as the previous day and every label shifts one
// column, leaving the headers out of step with the dates underneath them.
export function weekdayShortLabels(locale: string) {
  return Array.from({ length: 7 }, (_, index) => {
    const date = new Date(Date.UTC(2024, 0, 7 + index)) // 2024-01-07 is a Sunday
    return date.toLocaleDateString(locale, { weekday: 'short', timeZone: 'UTC' })
  })
}
