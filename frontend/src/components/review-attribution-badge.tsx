import { Bot, CircleHelp, UserRound } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Transaction } from '@/types'

export function ReviewAttributionBadge({ transaction }: { transaction: Transaction }) {
  const { t } = useTranslation()

  if (transaction.virtual || transaction.review_status !== 'reviewed') return null

  // Only the server's nested packet attests an actor; legacy flat stamps do not.
  const kind = transaction.review_attribution?.kind
  const Icon = kind === 'human' ? UserRound : kind === 'rule' ? Bot : CircleHelp
  const label = kind === 'human'
    ? t('transactions.reviewedByHuman')
    : kind === 'rule'
      ? t('transactions.reviewedByRule')
      : t('transactions.reviewedUnattributed')

  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className="shrink-0 inline-flex items-center justify-center text-muted-foreground"
    >
      <Icon size={12} aria-hidden="true" />
    </span>
  )
}
