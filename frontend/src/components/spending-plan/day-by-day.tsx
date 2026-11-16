import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import {
  allowanceOn,
  COMFORT_MARGIN,
  endOfMonth,
  eventsByDate,
  monthsBetween,
  planToday,
} from '@/lib/spending-plan'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import type { SpendingPlanEvent, SpendingPlanTimeline } from '@/types'
import { BalanceLookup } from './balance-lookup'
import { PlanCalendar, type PlanDayView } from './plan-calendar'
import { TONE_BAND, type PlanFormat } from './plan-format'
import { PlanSheet } from './plan-sheet'

type View = 'calendar' | 'sheet'
const NO_EVENTS: SpendingPlanEvent[] = []

/** The month day by day, beside the balance on the chosen day. */
export function DayByDay({ timeline, format }: { timeline: SpendingPlanTimeline; format: PlanFormat }) {
  const { t } = useTranslation()
  const today = planToday(timeline)
  const horizonEnd = timeline.horizon_end
  const clamp = (date: string) => (date < today ? today : date > horizonEnd ? horizonEnd : date)
  const [view, setView] = useState<View>('calendar')
  const [picked, setPicked] = useState<string | null>(null)
  const selected = clamp(picked ?? endOfMonth(today))
  const months = monthsBetween(today, horizonEnd)
  const [shownMonth, setShownMonth] = useState(selected.slice(0, 7))
  const month = months.includes(shownMonth) ? shownMonth : months[0]
  const monthIndex = months.indexOf(month)

  const byDate = useMemo(() => eventsByDate(timeline.events), [timeline.events])
  const balances = useMemo(() => new Map(timeline.points.map((p) => [p.date, p.balance])), [timeline.points])
  const select = (date: string) => {
    setPicked(date)
    setShownMonth(date.slice(0, 7))
  }
  const dayView: PlanDayView = {
    today,
    floor: timeline.balance_floor,
    month,
    selected,
    balances,
    eventsOn: (day) => byDate.get(day) ?? NO_EVENTS,
    onSelect: select,
    format,
  }

  return (
    <section className="mb-5">
      <h2 className="mb-2 text-sm font-semibold">{t('spendingPlan.dayByDay')}</h2>
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Card className="gap-0 p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Button
                type="button"
                size="icon-sm"
                variant="outline"
                aria-label={t('spendingPlan.previousMonth')}
                disabled={monthIndex <= 0}
                onClick={() => setShownMonth(months[monthIndex - 1])}
              >
                <ChevronLeft />
              </Button>
              <h3 className="min-w-40 text-center text-sm font-semibold">{format.monthTitle(month)}</h3>
              <Button
                type="button"
                size="icon-sm"
                variant="outline"
                aria-label={t('spendingPlan.nextMonth')}
                disabled={monthIndex >= months.length - 1}
                onClick={() => setShownMonth(months[monthIndex + 1])}
              >
                <ChevronRight />
              </Button>
            </div>
            <div className="inline-flex overflow-hidden rounded-lg border border-border text-sm">
              {(['calendar', 'sheet'] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={view === option}
                  onClick={() => setView(option)}
                  className={cn('px-3 py-1', view === option ? 'bg-foreground text-background' : 'hover:bg-muted')}
                >
                  {t(option === 'calendar' ? 'spendingPlan.viewCalendar' : 'spendingPlan.viewSheet')}
                </button>
              ))}
            </div>
          </div>
          {view === 'calendar' ? (
            <PlanCalendar {...dayView} />
          ) : (
            <PlanSheet {...dayView} dailyOn={(day) => allowanceOn(day, timeline)} />
          )}
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <LegendSwatch className={TONE_BAND.good}>
              {t('spendingPlan.legendGood', { amount: format.money(timeline.balance_floor + COMFORT_MARGIN) })}
            </LegendSwatch>
            <LegendSwatch className={TONE_BAND.warn}>{t('spendingPlan.legendWarn')}</LegendSwatch>
            <LegendSwatch className={TONE_BAND.bad}>
              {t('spendingPlan.legendBad', { amount: format.money(timeline.balance_floor) })}
            </LegendSwatch>
            <span>{t('spendingPlan.legendHint')}</span>
          </div>
        </Card>
        <BalanceLookup timeline={timeline} date={selected} onDateChange={select} format={format} />
      </div>
    </section>
  )
}

function LegendSwatch({ className, children }: { className: string; children: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn('h-1 w-3 rounded-full', className)} />
      {children}
    </span>
  )
}
