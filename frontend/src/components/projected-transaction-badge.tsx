import { useTranslation } from 'react-i18next'

/** Canonical badge for a virtual transaction projection. */
export function ProjectedTransactionBadge() {
  const { t } = useTranslation()

  return (
    <span className="inline-flex items-center rounded-full border border-violet-200 bg-violet-50 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-violet-700 shrink-0 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-300">
      {t('transactions.projected')}
    </span>
  )
}
