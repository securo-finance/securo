import { useTranslation } from 'react-i18next'
import { balanceTone, dayEntries, gridFrom } from '@/lib/spending-plan'
import { cn } from '@/lib/utils'
import type { SpendingPlanEvent } from '@/types'
import { entryLabel, entryTone, TONE_BAND, TONE_TEXT, type PlanFormat } from './plan-format'

export interface PlanDayView {
  today: string
  floor: number
  month: string
  selected: string
  balances: Map<string, number>
  eventsOn: (day: string) => SpendingPlanEvent[]
  onSelect: (day: string) => void
  format: PlanFormat
}

/** A month of projected end-of-day balances, with each day's biggest moves. */
export function PlanCalendar({ today, floor, month, selected, balances, eventsOn, onSelect, format }: PlanDayView) {
  const { t } = useTranslation()
  return (
    <div className="grid grid-cols-7 gap-1">
      {[0, 1, 2, 3, 4, 5, 6].map((weekday) => (
        <div key={weekday} className="px-1.5 pb-1 text-[11px] uppercase tracking-wide text-muted-foreground">
          {format.weekdayShort(weekday)}
        </div>
      ))}
      {gridFrom(month, today).map((day, index) => {
        if (day === null) return <div key={`pad-${index}`} />
        const dayNumber = Number(day.slice(8))
        const balance = balances.get(day)
        if (day < today || balance === undefined) {
          return (
            <div key={day} className="min-h-20 rounded-lg border border-border p-1.5 text-xs text-muted-foreground opacity-50">
              {dayNumber}
            </div>
          )
        }
        const entries = dayEntries(eventsOn(day))
        const tone = balanceTone(balance, floor)
        return (
          <button
            key={day}
            type="button"
            aria-label={format.longDate(day)}
            aria-pressed={day === selected}
            onClick={() => onSelect(day)}
            className={cn(
              'relative flex min-h-20 min-w-0 flex-col gap-0.5 overflow-hidden rounded-lg border border-border bg-card px-1.5 pb-2 pt-1 text-left transition-colors hover:border-muted-foreground/40',
              day === today && 'ring-2 ring-inset ring-primary',
              day === selected && 'ring-2 ring-inset ring-foreground',
            )}
          >
            <span className="flex justify-between text-xs text-muted-foreground">
              <span>{dayNumber}</span>
              {day === today && <span className="text-primary">{t('spendingPlan.today')}</span>}
            </span>
            <span className={cn('text-sm font-semibold tabular-nums', TONE_TEXT[tone])}>{format.compact(balance)}</span>
            {entries.slice(0, 2).map((entry, entryIndex) => (
              <span key={entryIndex} className={cn('truncate text-[10.5px] leading-tight', entryTone(entry))}>
                {format.signedCompact(entry.amount)} {entryLabel(entry, t)}
              </span>
            ))}
            {entries.length > 2 && (
              <span className="text-[10.5px] leading-tight text-muted-foreground">
                {t('spendingPlan.moreEvents', { count: entries.length - 2 })}
              </span>
            )}
            <span aria-hidden className={cn('absolute inset-x-0 bottom-0 h-1', TONE_BAND[tone])} />
          </button>
        )
      })}
    </div>
  )
}
