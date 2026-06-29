import { useState } from 'react'
import {
  startOfMonth,
  endOfMonth,
  startOfWeek,
  endOfWeek,
  addDays,
  addMonths,
  subMonths,
  addYears,
  subYears,
  isSameMonth,
  isSameDay,
  isToday,
  format,
  setMonth as dfSetMonth,
  setYear as dfSetYear,
  type Locale,
} from 'date-fns'
import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface CalendarProps {
  mode?: 'single'
  selected?: Date
  defaultMonth?: Date
  /**
   * Open directly at this view, skipping the climb-through. The default
   * 'days' preserves the original behavior for callers that just need a
   * day picker. The PeriodSelector uses 'months' so clicking the value
   * opens the month grid without first showing days.
   */
  initialView?: 'days' | 'months' | 'years'
  locale?: Locale
  /** Fired when a day cell is clicked (existing behavior). */
  onSelect?: (date: Date | undefined) => void
  /**
   * Fired when a month cell is clicked. When set, the calendar stops at the
   * month view (no auto-climb-back to days) so callers can react to a
   * month-level pick without an extra click.
   */
  onSelectMonth?: (date: Date) => void
  /** Fired when a year cell is clicked. Same stop-at-this-view semantics. */
  onSelectYear?: (date: Date) => void
  className?: string
}

type View = 'days' | 'months' | 'years'
const YEAR_PAGE_SIZE = 12

function Calendar({
  selected,
  defaultMonth,
  initialView = 'days',
  locale,
  onSelect,
  onSelectMonth,
  onSelectYear,
  className,
}: CalendarProps) {
  const [viewMonth, setViewMonth] = useState(defaultMonth ?? selected ?? new Date())
  const [view, setView] = useState<View>(initialView)

  const currentYear = viewMonth.getFullYear()
  const yearPageStart = Math.floor(currentYear / YEAR_PAGE_SIZE) * YEAR_PAGE_SIZE

  const goPrev = () => {
    if (view === 'days') setViewMonth(subMonths(viewMonth, 1))
    else if (view === 'months') setViewMonth(subYears(viewMonth, 1))
    else setViewMonth(subYears(viewMonth, YEAR_PAGE_SIZE))
  }
  const goNext = () => {
    if (view === 'days') setViewMonth(addMonths(viewMonth, 1))
    else if (view === 'months') setViewMonth(addYears(viewMonth, 1))
    else setViewMonth(addYears(viewMonth, YEAR_PAGE_SIZE))
  }

  const headerLabel =
    view === 'days'
      ? format(viewMonth, 'LLLL yyyy', { locale })
      : view === 'months'
        ? format(viewMonth, 'yyyy', { locale })
        : `${yearPageStart} – ${yearPageStart + YEAR_PAGE_SIZE - 1}`

  const onHeaderClick = () => {
    if (view === 'days') setView('months')
    else if (view === 'months') setView('years')
  }

  // Granular pick handlers: when a granular callback is provided, the
  // calendar stops at that view (no auto-climb-back to days) so callers can
  // react to a single click without forcing an extra step.
  const handleMonthPick = (monthDate: Date) => {
    if (onSelectMonth) {
      onSelectMonth(monthDate)
      return
    }
    setViewMonth(monthDate)
    setView('days')
  }
  const handleYearPick = (year: number) => {
    const next = dfSetYear(viewMonth, year)
    if (onSelectYear) {
      onSelectYear(next)
      return
    }
    setViewMonth(next)
    setView('months')
  }

  const monthStart = startOfMonth(viewMonth)
  const monthEnd = endOfMonth(viewMonth)
  const calStart = startOfWeek(monthStart, { locale })
  const calEnd = endOfWeek(monthEnd, { locale })

  const weeks: Date[][] = []
  let day = calStart
  while (day <= calEnd) {
    const week: Date[] = []
    for (let i = 0; i < 7; i++) {
      week.push(day)
      day = addDays(day, 1)
    }
    weeks.push(week)
  }

  const weekdayLabels: string[] = []
  for (let i = 0; i < 7; i++) {
    weekdayLabels.push(format(addDays(calStart, i), 'EEEEEE', { locale }))
  }

  return (
    <div className={cn('p-3 w-[252px]', className)}>
      {/* Header: nav + caption */}
      <div className="flex items-center justify-between mb-3">
        <button
          type="button"
          onClick={goPrev}
          className="size-7 inline-flex items-center justify-center rounded-lg border border-border bg-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
        >
          <ChevronLeftIcon className="size-4" />
        </button>
        <button
          type="button"
          onClick={onHeaderClick}
          disabled={view === 'years'}
          className={cn(
            'text-sm font-medium text-foreground capitalize px-2 py-1 rounded-md transition-colors',
            view !== 'years' && 'hover:bg-muted/60 cursor-pointer',
            view === 'years' && 'cursor-default',
          )}
        >
          {headerLabel}
        </button>
        <button
          type="button"
          onClick={goNext}
          className="size-7 inline-flex items-center justify-center rounded-lg border border-border bg-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
        >
          <ChevronRightIcon className="size-4" />
        </button>
      </div>

      {view === 'days' && (
        <>
          <div className="grid grid-cols-7 mb-1">
            {weekdayLabels.map((label, i) => (
              <div key={i} className="text-center text-[11px] font-medium text-muted-foreground py-1">
                {label}
              </div>
            ))}
          </div>
          <div className="grid grid-cols-7">
            {weeks.map((week, wi) =>
              week.map((d, di) => {
                const inMonth = isSameMonth(d, viewMonth)
                const isSelected = selected && isSameDay(d, selected)
                const today = isToday(d)

                return (
                  <button
                    key={`${wi}-${di}`}
                    type="button"
                    onClick={() => onSelect?.(d)}
                    className={cn(
                      'size-8 inline-flex items-center justify-center rounded-lg text-sm transition-colors',
                      !inMonth && 'text-muted-foreground/40',
                      inMonth && !isSelected && 'text-foreground hover:bg-muted/60',
                      today && !isSelected && 'bg-accent text-accent-foreground font-medium',
                      isSelected && 'bg-primary text-primary-foreground font-semibold',
                    )}
                  >
                    {d.getDate()}
                  </button>
                )
              }),
            )}
          </div>
        </>
      )}

      {view === 'months' && (
        <div className="grid grid-cols-3 gap-1">
          {Array.from({ length: 12 }).map((_, i) => {
            const monthDate = dfSetMonth(viewMonth, i)
            const isCurrent =
              selected &&
              selected.getFullYear() === monthDate.getFullYear() &&
              selected.getMonth() === i
            return (
              <button
                key={i}
                type="button"
                onClick={() => handleMonthPick(monthDate)}
                className={cn(
                  'h-10 rounded-lg text-sm capitalize transition-colors',
                  isCurrent
                    ? 'bg-primary text-primary-foreground font-semibold'
                    : 'text-foreground hover:bg-muted/60',
                )}
              >
                {format(monthDate, 'MMM', { locale })}
              </button>
            )
          })}
        </div>
      )}

      {view === 'years' && (
        <div className="grid grid-cols-3 gap-1">
          {Array.from({ length: YEAR_PAGE_SIZE }).map((_, i) => {
            const y = yearPageStart + i
            const isCurrent = selected && selected.getFullYear() === y
            return (
              <button
                key={y}
                type="button"
                onClick={() => handleYearPick(y)}
                className={cn(
                  'h-10 rounded-lg text-sm transition-colors',
                  isCurrent
                    ? 'bg-primary text-primary-foreground font-semibold'
                    : 'text-foreground hover:bg-muted/60',
                )}
              >
                {y}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export { Calendar }
export type { CalendarProps }
