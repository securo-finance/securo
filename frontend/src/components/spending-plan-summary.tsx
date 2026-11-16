import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { CalendarCheck } from 'lucide-react'
import { spendingPlan as spendingPlanApi } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { cn } from '@/lib/utils'
import { coverageLabel } from '@/lib/spending-plan'

/** The dashboard strip: what is left to spend today and until when the money lasts. */
export function SpendingPlanSummary() {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { data: plan } = useQuery({
    queryKey: ['spending-plan'],
    queryFn: spendingPlanApi.get,
  })
  const { data: timeline } = useQuery({
    queryKey: ['dashboard', 'spending-plan', 'timeline', null],
    queryFn: () => spendingPlanApi.timeline(),
    enabled: !!plan,
  })
  if (!plan || !timeline) return null

  const remaining = timeline.today.remaining
  // Red when the money runs out before the horizon's floor, amber when only
  // today went over, green when both are fine.
  const tone = !timeline.covered_through_horizon ? 'bad' : remaining < 0 ? 'warn' : 'good'
  return (
    <Link
      to="/plan"
      className={cn(
        'w-full flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-lg border px-4 py-2.5 transition-colors',
        tone === 'good' &&
          'bg-emerald-50 border-emerald-200 hover:bg-emerald-100 dark:bg-emerald-500/10 dark:border-emerald-500/30 dark:hover:bg-emerald-500/20',
        tone === 'warn' &&
          'bg-amber-50 border-amber-200 hover:bg-amber-100 dark:bg-amber-500/10 dark:border-amber-500/30 dark:hover:bg-amber-500/20',
        tone === 'bad' &&
          'bg-rose-50 border-rose-200 hover:bg-rose-100 dark:bg-rose-500/10 dark:border-rose-500/30 dark:hover:bg-rose-500/20',
      )}
    >
      <span className="flex items-center gap-2 text-sm text-foreground">
        <CalendarCheck
          size={16}
          className={tone === 'good' ? 'text-emerald-600' : tone === 'warn' ? 'text-amber-600' : 'text-rose-600'}
        />
        {remaining >= 0
          ? t('spendingPlan.todayRemaining', {
              amount: mask(formatCurrency(remaining, timeline.currency, locale)),
            })
          : t('spendingPlan.todayOver', {
              amount: mask(formatCurrency(-remaining, timeline.currency, locale)),
            })}
      </span>
      <span className="text-xs text-muted-foreground">{coverageLabel(timeline, dateLocale, t)}</span>
    </Link>
  )
}
