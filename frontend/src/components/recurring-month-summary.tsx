import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { recurring as recurringApi } from '@/lib/api'
import { currentMonth } from '@/lib/month-utils'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { MonthStepper } from '@/components/month-stepper'

/**
 * Income, expenses and net the active recurring definitions expect in the
 * selected month (#532). The backend counts each occurrence in the month, so
 * a weekly definition adds four or five times and a yearly one only in its
 * own month; transfers stay out of the totals.
 */
export function RecurringMonthSummary() {
  const { t, i18n } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const [month, setMonth] = useState(currentMonth)

  const { data, isError } = useQuery({
    queryKey: ['recurring', 'month-summary', month],
    queryFn: () => recurringApi.monthSummary(month),
  })

  const money = (value: string) => mask(formatCurrency(Number(value), data?.currency, locale))
  const net = data ? Number(data.net) : 0

  return (
    <div
      className="flex flex-col sm:flex-row flex-wrap sm:items-center gap-x-5 gap-y-2 border-b border-border bg-muted/30 px-4 sm:px-5 py-2.5"
      aria-live="polite"
    >
      <MonthStepper
        value={month}
        onChange={setMonth}
        locale={i18n.resolvedLanguage ?? i18n.language}
        prevLabel={t('common.previous')}
        nextLabel={t('common.next')}
      />
      {isError ? (
        <span className="text-xs text-muted-foreground">{t('recurring.summaryFailed')}</span>
      ) : data ? (
        <>
          <span className="flex items-baseline gap-1.5 text-xs">
            <span className="text-muted-foreground">{t('recurring.summaryIncome')}</span>
            <span className="text-sm font-semibold tabular-nums text-emerald-600">
              {money(data.income)}
            </span>
          </span>
          <span className="flex items-baseline gap-1.5 text-xs">
            <span className="text-muted-foreground">{t('recurring.summaryExpenses')}</span>
            <span className="text-sm font-semibold tabular-nums text-rose-500">
              {money(data.expenses)}
            </span>
          </span>
          <span className="flex items-baseline gap-1.5 text-xs">
            <span className="text-muted-foreground">{t('recurring.summaryNet')}</span>
            <span
              className={`text-sm font-bold tabular-nums ${net >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}
            >
              {money(data.net)}
            </span>
          </span>
          {data.transfer_count > 0 && (
            <span className="text-xs text-muted-foreground">
              {t('recurring.summaryTransfers', { count: data.transfer_count })}
            </span>
          )}
          {data.unconverted_count > 0 && (
            <span className="text-xs text-amber-600">
              {t('recurring.summaryUnconverted', { count: data.unconverted_count })}
            </span>
          )}
        </>
      ) : null}
    </div>
  )
}
