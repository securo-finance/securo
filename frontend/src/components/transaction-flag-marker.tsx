import { Flag } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { formatDate } from '@/lib/date-range-format'
import { flagState } from '@/lib/transaction-flag'
import { cn } from '@/lib/utils'
import type { Transaction } from '@/types'

/**
 * The flag shown on a row. Amber while flagged, red once its reminder date
 * has passed; the date and note are in the tooltip and the accessible name.
 */
export function TransactionFlagMarker({
  tx,
  locale,
  className,
}: {
  tx: Pick<Transaction, 'is_flagged' | 'flag_due_date' | 'flag_note'>
  /** Locale for the reminder date (follows the date-format setting). */
  locale: string
  className?: string
}) {
  const { t } = useTranslation()
  const state = flagState(tx)
  if (state === 'none') return null
  // The date (and whether it is past) and the note, for the tooltip.
  const parts = [state === 'overdue' ? t('transactions.flagOverdue') : t('transactions.flagged')]
  if (tx.flag_due_date) {
    parts.push(t('transactions.flagDueOn', { date: formatDate(tx.flag_due_date, locale) }))
  }
  if (tx.flag_note) parts.push(tx.flag_note)
  const text = parts.join(' · ')
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center',
        state === 'overdue' ? 'text-rose-600 dark:text-rose-400' : 'text-amber-600 dark:text-amber-400',
        className,
      )}
      title={text}
      aria-label={text}
      role="img"
      data-flag-state={state}
    >
      <Flag className="h-3.5 w-3.5 fill-current" />
    </span>
  )
}
