import { useTranslation } from 'react-i18next'
import { Check, X } from 'lucide-react'
import { firstDayBelowFloor, planToday } from '@/lib/spending-plan'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import type { SpendingPlanTimeline } from '@/types'
import type { PlanFormat } from './plan-format'
import type { WhatIfItem } from './use-what-if-items'
import { WhatIfList } from './what-if-list'

/** Today's amount, the daily allowance to try out, and the what-ifs to count. */
export function PlanHero({
  timeline,
  allowance,
  savedAllowance,
  onAllowanceChange,
  onSave,
  saving,
  canWrite,
  whatIfItems,
  onWhatIfChange,
  format,
}: {
  timeline: SpendingPlanTimeline
  allowance: number
  savedAllowance: number
  onAllowanceChange: (value: number) => void
  onSave: () => void
  saving: boolean
  canWrite: boolean
  whatIfItems: WhatIfItem[]
  onWhatIfChange: (items: WhatIfItem[]) => void
  format: PlanFormat
}) {
  const { t } = useTranslation()
  const { money } = format
  const safe = timeline.safe_daily_allowance
  const remaining = timeline.today.remaining
  const belowOn = firstDayBelowFloor(timeline)
  const difference = Math.round((allowance - safe) * 100) / 100
  const sliderMax = Math.max(10, Math.ceil((Math.max(safe, allowance, savedAllowance) * 1.5) / 10) * 10)

  return (
    <Card className="mb-5 grid gap-6 p-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <div className="min-w-0">
        <p className="text-sm text-muted-foreground">{t('spendingPlan.todayCanSpend')}</p>
        <p
          className={cn(
            'text-4xl font-bold tracking-tight tabular-nums',
            remaining < 0 ? 'text-rose-500' : 'text-foreground',
          )}
        >
          {money(remaining)}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {t('spendingPlan.todayCaption', {
            spent: money(timeline.today.spent),
            allowance: money(timeline.today.allowance),
          })}
        </p>
        <p
          className={cn(
            'mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-sm',
            belowOn === null
              ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400'
              : 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-400',
          )}
        >
          {belowOn === null ? (
            <Check size={16} className="mt-0.5 shrink-0" />
          ) : (
            <X size={16} className="mt-0.5 shrink-0" />
          )}
          {belowOn === null
            ? t('spendingPlan.coverageOk', {
                amount: money(timeline.daily_allowance),
                floor: money(timeline.balance_floor),
                date: format.shortDate(timeline.horizon_end),
              })
            : t('spendingPlan.coverageBreak', {
                amount: money(timeline.daily_allowance),
                floor: money(timeline.balance_floor),
                date: format.shortDate(belowOn),
              })}
        </p>
      </div>

      <div className="min-w-0 space-y-4">
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
            <label htmlFor="plan-slider" className="text-muted-foreground">
              {t('spendingPlan.dailyAllowance')}
            </label>
            <span>
              {t('spendingPlan.safeShort')} <span className="font-semibold tabular-nums">{money(safe)}</span>{' '}
              {difference !== 0 && (
                <span className={cn('text-xs', difference > 0 ? 'text-rose-500' : 'text-muted-foreground')}>
                  {difference > 0
                    ? t('spendingPlan.allowanceAbove', { amount: money(difference) })
                    : t('spendingPlan.allowanceBelow', { amount: money(-difference) })}
                </span>
              )}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <input
              id="plan-slider"
              type="range"
              min={0}
              max={sliderMax}
              step={1}
              value={allowance}
              onChange={(event) => onAllowanceChange(Number(event.target.value))}
              className="h-2 w-full cursor-pointer accent-primary"
            />
            <span className="w-24 shrink-0 text-right text-sm font-semibold tabular-nums">{money(allowance)}</span>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">{t('spendingPlan.sliderHint')}</span>
            <div className="flex gap-2">
              {difference !== 0 && (
                <Button type="button" size="xs" variant="outline" onClick={() => onAllowanceChange(safe)}>
                  {t('spendingPlan.useSafe', { amount: money(safe) })}
                </Button>
              )}
              {canWrite && allowance !== savedAllowance && (
                <Button type="button" size="xs" onClick={onSave} disabled={saving}>
                  {t('spendingPlan.saveAllowance', { amount: money(allowance) })}
                </Button>
              )}
            </div>
          </div>
        </div>
        <WhatIfList items={whatIfItems} onChange={onWhatIfChange} today={planToday(timeline)} format={format} />
      </div>
    </Card>
  )
}
