import { Check } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { shouldShowPendingBadge } from '@/lib/transaction-status'

export type PostTodayButtonProps = {
  id: string
  status: string | null
  source: string | null
  canWrite: boolean
  onPostToday: (id: string) => void
}

/**
 * One-click "it cleared today" action for a pending row. Sits next to the
 * pending badge and posts the transaction with today's date from a single
 * click, instead of opening the edit dialog. Shown only for rows the pending
 * badge applies to, and only to users who can write the workspace.
 */
export function PostTodayButton({ id, status, source, canWrite, onPostToday }: PostTodayButtonProps) {
  const { t } = useTranslation()
  if (!canWrite || !shouldShowPendingBadge({ status, source })) return null

  const label = t('transactions.postToday')
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation()
        onPostToday(id)
      }}
      className="shrink-0 inline-flex items-center justify-center rounded-full border border-amber-300 bg-amber-50 p-0.5 text-amber-500 transition-colors hover:bg-amber-100 dark:border-amber-500/40 dark:bg-amber-500/10 dark:hover:bg-amber-500/20"
    >
      <Check size={12} />
    </button>
  )
}
