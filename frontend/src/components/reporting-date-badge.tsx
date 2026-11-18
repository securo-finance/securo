import { CalendarClock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { listDate, type DatedTransaction } from '@/lib/list-date'

/** Keep both dates readable on touch screens and by assistive technology. */
export function ReportingDateBadge({ tx, locale }: { tx: DatedTransaction; locale: string }) {
  const { t } = useTranslation()
  // The user's own reporting date wins. The card statement view files a
  // row by its bill date, so its `reporting_date` leaves the override out.
  const reportingDate = tx.reporting_date_override ?? listDate(tx)
  const showsReportingDate = reportingDate !== tx.date
  // The Date column shows `listDate`; when that is not the bank date, the
  // bank date stays readable here even if the override equals it.
  if (!showsReportingDate && listDate(tx) === tx.date) return null

  return (
    <span className="inline-flex max-w-full flex-wrap items-center gap-1 text-xs text-violet-600 dark:text-violet-400">
      <CalendarClock className="h-3 w-3 shrink-0" aria-hidden="true" />
      {showsReportingDate && (
        <span>
          {t('transactions.reportingDate')}: {new Date(reportingDate + 'T00:00:00').toLocaleDateString(locale)}
        </span>
      )}
      <span>
        {showsReportingDate && '· '}
        {t('transactions.bankDate')}: {new Date(tx.date + 'T00:00:00').toLocaleDateString(locale)}
      </span>
    </span>
  )
}
