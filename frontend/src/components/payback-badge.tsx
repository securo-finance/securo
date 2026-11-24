import { useTranslation } from 'react-i18next'
import type { Transaction } from '@/types'

/** A credit recorded as a payback that settles a group debt (#424). */
export function PaybackBadge({ tx }: { tx: Pick<Transaction, 'settled_by'> }) {
  const { t } = useTranslation()
  if (!tx.settled_by?.length) return null

  return (
    <span className="inline-flex items-center rounded-full border border-violet-200 bg-violet-50 px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-violet-700 shrink-0 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-300">
      {t('splitGroups.shareSettlement')}
    </span>
  )
}
