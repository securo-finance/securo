import { useState } from 'react'
import { shiftMonth, monthLabel } from '@/lib/month-utils'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { MonthPicker } from '@/components/ui/monthpicker'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'

interface MonthStepperProps {
  /** Selected month as `"YYYY-MM"`. */
  value: string
  /** Called with the new `"YYYY-MM"` when the user steps to another month. */
  onChange: (yearMonth: string) => void
  /** BCP-47 locale for the month label (e.g. "pt-BR", "en-US"). */
  locale?: string
  /** Accessible labels for the prev/next buttons. */
  prevLabel?: string
  nextLabel?: string
}

/**
 * Compact month stepper: `‹  Month Year  ›`. Stateless — it only renders the
 * given month and emits onChange. Wiring (URL/date-range/query) lives in the
 * parent so the stepper stays a single source of truth on top of existing filters.
 */
export function MonthStepper({ value, onChange, locale = 'pt-BR', prevLabel, nextLabel }: MonthStepperProps) {
  const [open, setOpen] = useState(false)
  const label = monthLabel(value, locale).replace(/^\w/, (c) => c.toUpperCase())
  const dateFnsLocale = resolveDateFnsLocale(locale)

  return (
    <div className="flex items-center gap-1 min-w-0">
      <button
        type="button"
        aria-label={prevLabel}
        className="h-8 w-8 shrink-0 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:text-foreground transition-all text-base cursor-pointer"
        onClick={() => onChange(shiftMonth(value, -1))}
      >
        &#8249;
      </button>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="inline-flex items-center justify-center border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground min-w-0 sm:min-w-[160px] truncate hover:bg-muted/50 transition-all cursor-pointer"
          >
            {label}
          </button>
        </PopoverTrigger>
        <PopoverContent align="center" className="w-auto p-0">
          <MonthPicker
            locale={dateFnsLocale}
            selectedMonth={new Date(`${value}-01T00:00:00`)}
            onMonthSelect={(date) => {
              if (!date) return
              const newMonth = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
              onChange(newMonth)
              setOpen(false)
            }}
          />
        </PopoverContent>
      </Popover>
      <button
        type="button"
        aria-label={nextLabel}
        className="h-8 w-8 shrink-0 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:text-foreground transition-all text-base cursor-pointer"
        onClick={() => onChange(shiftMonth(value, 1))}
      >
        &#8250;
      </button>
    </div>
  )
}

