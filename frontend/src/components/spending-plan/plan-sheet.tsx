import { useTranslation } from 'react-i18next'
import { balanceTone, dayEntries, monthGrid, weekday } from '@/lib/spending-plan'
import { cn } from '@/lib/utils'
import type { PlanDayView } from './plan-calendar'
import { entryLabel, INCOME_TEXT, SCENARIO_TEXT, TONE_BAND, TONE_TEXT } from './plan-format'

const TH = 'px-2 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-muted-foreground'

/** One row per day ahead in the month: what happens, what moves, what is left. */
export function PlanSheet({
  today,
  floor,
  month,
  selected,
  balances,
  eventsOn,
  onSelect,
  format,
  dailyOn,
}: PlanDayView & { dailyOn: (day: string) => number }) {
  const { t } = useTranslation()
  const days = monthGrid(month).filter((day): day is string => day !== null && day >= today && balances.has(day))
  return (
    <div className="max-h-[620px] overflow-auto rounded-lg">
      <table className="w-full border-collapse text-sm">
        <thead className="sticky top-0 bg-card">
          <tr className="border-b border-border">
            <th className={cn(TH, 'text-left')}>{t('spendingPlan.sheetDay')}</th>
            <th className={cn(TH, 'text-left')}>{t('spendingPlan.sheetWhat')}</th>
            <th className={TH}>{t('spendingPlan.sheetIn')}</th>
            <th className={TH}>{t('spendingPlan.sheetOut')}</th>
            <th className={TH}>{t('spendingPlan.dailyAllowance')}</th>
            <th className={TH}>{t('spendingPlan.sheetBalance')}</th>
            <th aria-hidden="true" className="w-14" />
          </tr>
        </thead>
        <tbody>
          {days.map((day) => {
            const events = eventsOn(day)
            const entries = dayEntries(events)
            const income = events.filter((e) => e.amount > 0).reduce((total, e) => total + e.amount, 0)
            const out = events.filter((e) => e.amount < 0).reduce((total, e) => total + e.amount, 0)
            const balance = balances.get(day) ?? 0
            const tone = balanceTone(balance, floor)
            return (
              <tr
                key={day}
                onClick={() => onSelect(day)}
                className={cn(
                  'cursor-pointer border-b border-border hover:bg-muted/40',
                  weekday(day) === 6 && 'border-b-2',
                  day === selected && 'bg-primary/10 hover:bg-primary/15',
                )}
              >
                <td className="whitespace-nowrap px-2 py-1.5 tabular-nums">
                  <button type="button" aria-pressed={day === selected} aria-label={format.longDate(day)}>
                    <span className="text-muted-foreground">{format.weekdayShort(weekday(day))}</span> {format.dayMonth(day)}
                  </button>
                </td>
                <td className="px-2 py-1.5">
                  {entries.length === 0 ? (
                    <span className="text-muted-foreground">—</span>
                  ) : entries.map((entry, index) => (
                    <span
                      key={index}
                      className={entry.kind === 'event' && entry.event.source === 'scenario' ? SCENARIO_TEXT : undefined}
                    >
                      {index > 0 && ', '}
                      {entryLabel(entry, t)}
                    </span>
                  ))}
                </td>
                <td className={cn('whitespace-nowrap px-2 py-1.5 text-right tabular-nums', INCOME_TEXT)}>
                  {income ? format.signed(income) : ''}
                </td>
                <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums">{out ? format.signed(out) : ''}</td>
                <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums text-muted-foreground">
                  {format.signed(-dailyOn(day))}
                </td>
                <td className={cn('whitespace-nowrap px-2 py-1.5 text-right font-semibold tabular-nums', TONE_TEXT[tone])}>
                  {format.money(balance)}
                </td>
                <td aria-hidden="true" className="px-2">
                  <div className={cn('h-1.5 rounded-full', TONE_BAND[tone])} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
