import { CalendarClock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Transaction } from '@/types'

/** Keep both dates readable on touch screens and by assistive technology. */
export function ReportingDateBadge({ tx, locale }: { tx: Pick<Transaction, 'date' | 'reporting_date_override'>; locale: string }) {
  const { t } = useTranslation()
  if (!tx.reporting_date_override || tx.reporting_date_override === tx.date) return null

  return (
    <span className="inline-flex max-w-full flex-wrap items-center gap-1 text-xs text-violet-600 dark:text-violet-400">
      <CalendarClock className="h-3 w-3 shrink-0" aria-hidden="true" />
      <span>{t('transactions.reportingDate')}: {new Date(tx.reporting_date_override + 'T00:00:00').toLocaleDateString(locale)}</span>
      <span>· {t('transactions.bankDate')}: {new Date(tx.date + 'T00:00:00').toLocaleDateString(locale)}</span>
    </span>
  )
}
