import { format } from 'date-fns'

export function localDateString(date = new Date()) {
  return format(date, 'yyyy-MM-dd')
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
