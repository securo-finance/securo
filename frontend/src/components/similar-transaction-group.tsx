import { ChevronRight, Rows3 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { SimilarTransactionGroup } from '@/types'
import { cn } from '@/lib/utils'
import { formatSimilarTransactionDateRange } from '@/lib/transaction-grouping'

export function SimilarTransactionGroupIcon({
  className,
  size = 17,
}: {
  className?: string
  size?: number
}) {
  return (
    <span className={cn('flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary', className)}>
      <Rows3 size={size} />
    </span>
  )
}

export function SimilarTransactionGroupSummary({
  group,
  locale,
  showDate,
  className,
}: {
  group: SimilarTransactionGroup
  locale: string
  showDate: boolean
  className?: string
}) {
  const { t } = useTranslation()
  return (
    <span className={className}>
      {showDate
        ? t('transactions.groupSummary', {
            count: group.transactions.length,
            dateRange: formatSimilarTransactionDateRange(group, locale),
          })
        : t('transactions.groupCount', { count: group.transactions.length })}
    </span>
  )
}

export function SimilarTransactionDisclosure({
  group,
  expanded,
  onToggle,
  className,
}: {
  group: SimilarTransactionGroup
  expanded: boolean
  onToggle: () => void
  className?: string
}) {
  const { t } = useTranslation()
  return (
    <button
      type="button"
      aria-expanded={expanded}
      aria-label={t(expanded ? 'transactions.collapseGroup' : 'transactions.expandGroup', {
        description: group.description,
      })}
      className={cn(
        'flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-background hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
        className,
      )}
      onClick={event => {
        event.stopPropagation()
        onToggle()
      }}
    >
      <ChevronRight size={16} className={cn('transition-transform', expanded && 'rotate-90')} />
    </button>
  )
}
