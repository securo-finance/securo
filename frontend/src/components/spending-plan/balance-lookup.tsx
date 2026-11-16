import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { lookupShortcuts, planToday, waterfallFor, type Bucket, type LookupShortcut } from '@/lib/spending-plan'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import type { SpendingPlanTimeline } from '@/types'
import { INCOME_TEXT, type PlanFormat } from './plan-format'

const TOP_ITEMS = 4

/** "How much will I have on X": the balance on a day and the math behind it. */
export function BalanceLookup({
  timeline,
  date,
  onDateChange,
  format,
}: {
  timeline: SpendingPlanTimeline
  date: string
  onDateChange: (date: string) => void
  format: PlanFormat
}) {
  const { t } = useTranslation()
  const today = planToday(timeline)
  const waterfall = waterfallFor(timeline, date)
  const shortcutLabel = (key: LookupShortcut, shortcutDate: string) => {
    switch (key) {
      case 'monthEnd': return t('spendingPlan.shortcutMonthEnd')
      case 'yearEnd': return format.dayMonth(shortcutDate)
      case 'threeMonths': return t('spendingPlan.months', { count: 3 })
      case 'sixMonths': return t('spendingPlan.months', { count: 6 })
      case 'horizon': return t('spendingPlan.shortcutHorizon')
    }
  }
  const topItems = (bucket: Bucket) => bucket.items
    .slice(0, TOP_ITEMS)
    .map((item) => `${item.label ?? t('spendingPlan.otherMoves')} ${format.signedCompact(item.amount)}`)
    .join(' · ')

  return (
    <Card className="gap-0 p-4 lg:sticky lg:top-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm text-muted-foreground">{t('spendingPlan.lookupTitle')}</span>
        <DatePickerInput
          value={date}
          onChange={(value) => {
            if (value >= today && value <= timeline.horizon_end) onDateChange(value)
          }}
          align="end"
        />
      </div>
      {waterfall && (
        <>
          <p
            data-testid="lookup-balance"
            className={cn(
              'mt-3 text-3xl font-bold tracking-tight tabular-nums',
              waterfall.balance < timeline.balance_floor ? 'text-rose-500' : 'text-foreground',
            )}
          >
            {format.money(waterfall.balance)}
          </p>
          <p className="mb-3 text-xs text-muted-foreground">
            {t('spendingPlan.lookupEndOfDay', { date: format.longDate(date) })}
          </p>
          <table className="w-full text-sm">
            <tbody className="[&_td]:border-b [&_td]:border-dashed [&_td]:border-border [&_td]:py-1.5 [&_td]:align-top">
              <WaterfallRow label={t('spendingPlan.receiptOpening')} amount={waterfall.opening} format={format} />
              {waterfall.income.items.length > 0 && (
                <WaterfallRow label={t('spendingPlan.groupIncome')} amount={waterfall.income.total} detail={topItems(waterfall.income)} format={format} />
              )}
              {waterfall.fixed.items.length > 0 && (
                <WaterfallRow label={t('spendingPlan.groupFixed')} amount={waterfall.fixed.total} detail={topItems(waterfall.fixed)} format={format} />
              )}
              {waterfall.card.items.length > 0 && (
                <WaterfallRow label={t('spendingPlan.cardBills')} amount={waterfall.card.total} detail={topItems(waterfall.card)} format={format} />
              )}
              {waterfall.scenario.items.length > 0 && (
                <WaterfallRow label={t('spendingPlan.whatIf')} amount={waterfall.scenario.total} detail={topItems(waterfall.scenario)} format={format} />
              )}
              {Math.round(waterfall.other * 100) !== 0 && (
                <WaterfallRow label={t('spendingPlan.otherMoves')} amount={waterfall.other} detail={t('spendingPlan.otherHint')} format={format} />
              )}
              <WaterfallRow
                label={t('spendingPlan.dailyTimes', { count: waterfall.days, amount: format.money(timeline.daily_allowance) })}
                amount={waterfall.daily}
                format={format}
              />
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-foreground font-bold">
                <td className="pt-2">{t('spendingPlan.sheetBalance')}</td>
                <td className="pt-2 text-right tabular-nums">{format.money(waterfall.balance)}</td>
              </tr>
            </tfoot>
          </table>
        </>
      )}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {lookupShortcuts(today, timeline.horizon_end).map(({ key, date: shortcutDate }) => (
          <button
            key={key}
            type="button"
            aria-pressed={shortcutDate === date}
            onClick={() => onDateChange(shortcutDate)}
            className={cn(
              'rounded-full border border-border px-2.5 py-0.5 text-xs transition-colors',
              shortcutDate === date ? 'border-foreground bg-foreground text-background' : 'hover:bg-muted',
            )}
          >
            {shortcutLabel(key, shortcutDate)}
          </button>
        ))}
      </div>
    </Card>
  )
}

function WaterfallRow({ label, amount, detail, format }: { label: ReactNode; amount: number; detail?: string; format: PlanFormat }) {
  return (
    <tr>
      <td className="pr-2">
        {label}
        {detail && <p className="text-xs text-muted-foreground">{detail}</p>}
      </td>
      <td className={cn('whitespace-nowrap text-right tabular-nums', amount > 0 && INCOME_TEXT)}>{format.signed(amount)}</td>
    </tr>
  )
}
