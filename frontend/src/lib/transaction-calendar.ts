export type CalendarDateBasis = 'reporting' | 'bank'

const CALENDAR_DATE_BASIS_STORAGE_KEY = 'securo.transactionCalendar.dateBasis'

export function readCalendarDateBasis(): CalendarDateBasis {
  if (typeof window === 'undefined') return 'reporting'
  return window.localStorage.getItem(CALENDAR_DATE_BASIS_STORAGE_KEY) === 'bank'
    ? 'bank'
    : 'reporting'
}

export function saveCalendarDateBasis(value: CalendarDateBasis): void {
  window.localStorage.setItem(CALENDAR_DATE_BASIS_STORAGE_KEY, value)
}
