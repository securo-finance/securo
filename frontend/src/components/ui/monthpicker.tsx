import * as React from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import { format, type Locale } from 'date-fns'

type Month = {
  number: number
  name: string
}

const MONTHS: Month[][] = [
  [
    { number: 0, name: 'Jan' },
    { number: 1, name: 'Feb' },
    { number: 2, name: 'Mar' },
    { number: 3, name: 'Apr' },
  ],
  [
    { number: 4, name: 'May' },
    { number: 5, name: 'Jun' },
    { number: 6, name: 'Jul' },
    { number: 7, name: 'Aug' },
  ],
  [
    { number: 8, name: 'Sep' },
    { number: 9, name: 'Oct' },
    { number: 10, name: 'Nov' },
    { number: 11, name: 'Dec' },
  ],
]

interface MonthPickerProps {
  selectedMonth?: Date
  onMonthSelect?: (date: Date) => void
  locale?: Locale
  className?: string
  minDate?: Date
  maxDate?: Date
}

function MonthPicker({
  selectedMonth,
  onMonthSelect,
  locale,
  className,
  minDate,
  maxDate,
}: MonthPickerProps) {
  const initialYear = selectedMonth?.getFullYear() ?? new Date().getFullYear()
  const [menuYear, setMenuYear] = React.useState<number>(initialYear)

  const selectedYear = selectedMonth?.getFullYear()
  const selectedMonthIdx = selectedMonth?.getMonth()

  const handlePrevYear = () => {
    setMenuYear((prev) => prev - 1)
  }

  const handleNextYear = () => {
    setMenuYear((prev) => prev + 1)
  }

  return (
    <div className={cn('p-3 w-[252px]', className)}>
      {/* Header: nav + year */}
      <div className="flex items-center justify-between mb-3">
        <button
          type="button"
          onClick={handlePrevYear}
          disabled={minDate ? menuYear - 1 < minDate.getFullYear() : false}
          className="size-7 inline-flex items-center justify-center rounded-lg border border-border bg-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors disabled:opacity-50 disabled:pointer-events-none cursor-pointer"
        >
          <ChevronLeft className="size-4" />
        </button>
        <span className="text-sm font-medium text-foreground capitalize px-2 py-1">
          {menuYear}
        </span>
        <button
          type="button"
          onClick={handleNextYear}
          disabled={maxDate ? menuYear + 1 > maxDate.getFullYear() : false}
          className="size-7 inline-flex items-center justify-center rounded-lg border border-border bg-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors disabled:opacity-50 disabled:pointer-events-none cursor-pointer"
        >
          <ChevronRight className="size-4" />
        </button>
      </div>

      {/* Months Grid */}
      <table className="w-full border-collapse space-y-1">
        <tbody>
          {MONTHS.map((monthRow, rIdx) => (
            <tr key={`row-${rIdx}`} className="flex w-full mt-2 gap-1">
              {monthRow.map((m) => {
                const monthDate = new Date(menuYear, m.number, 1)
                const isSelected = selectedYear === menuYear && selectedMonthIdx === m.number

                let isDisabled = false
                if (minDate) {
                  if (menuYear < minDate.getFullYear()) {
                    isDisabled = true
                  } else if (menuYear === minDate.getFullYear() && m.number < minDate.getMonth()) {
                    isDisabled = true
                  }
                }
                if (maxDate) {
                  if (menuYear > maxDate.getFullYear()) {
                    isDisabled = true
                  } else if (menuYear === maxDate.getFullYear() && m.number > maxDate.getMonth()) {
                    isDisabled = true
                  }
                }

                const displayMonthName = locale
                  ? format(monthDate, 'MMM', { locale })
                  : m.name

                return (
                  <td key={m.number} className="h-10 w-1/4 text-center text-sm p-0 relative">
                    <button
                      type="button"
                      disabled={isDisabled}
                      onClick={() => onMonthSelect?.(monthDate)}
                      className={cn(
                        'h-full w-full rounded-lg text-sm capitalize transition-colors flex items-center justify-center cursor-pointer',
                        isSelected
                          ? 'bg-primary text-primary-foreground font-semibold'
                          : 'text-foreground hover:bg-muted/60 disabled:opacity-30 disabled:pointer-events-none'
                      )}
                    >
                      {displayMonthName}
                    </button>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

MonthPicker.displayName = 'MonthPicker'

export { MonthPicker }
