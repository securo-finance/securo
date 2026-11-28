import { useTranslation } from 'react-i18next'
import { Undo2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatCurrency, ltrAmount } from '@/lib/format'
import {
  formatReimbursementNet,
  isFullyAllocatedReimbursement,
  type ReimbursementSource,
} from '@/lib/reimbursements'

/** "Reimbursement" where a fully linked counterpart's category would be. */
export function ReimbursementBadge({ className, compact = false }: { className?: string; compact?: boolean }) {
  const { t } = useTranslation()
  return (
    <span
      title={t('reimbursements.badgeTooltip')}
      className={cn(
        'inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide',
        'text-teal-700 bg-teal-50 border border-teal-200',
        'dark:bg-teal-950/40 dark:text-teal-300 dark:border-teal-900',
        'px-1.5 py-0.5 rounded-full',
        className,
      )}
    >
      <Undo2 size={10} aria-hidden="true" />
      <span className={compact ? 'sr-only' : undefined}>{t('reimbursements.badge')}</span>
    </span>
  )
}

/** The small line under a linked row's amount: the anchor's net, or what is left of a counterpart. */
export function ReimbursementAmountNote({
  tx,
  locale,
  mask = (value) => value,
}: {
  tx: ReimbursementSource
  locale: string
  mask?: (value: string) => string
}) {
  const { t } = useTranslation()
  const net = formatReimbursementNet(tx, locale)
  if (net != null) {
    return (
      <p className="text-[10px] text-muted-foreground tabular-nums">
        {t('reimbursements.rowNet', { amount: ltrAmount(mask(net)) })}
      </p>
    )
  }
  if (tx.reimbursement_role === 'counterpart' && !isFullyAllocatedReimbursement(tx)) {
    const left = formatCurrency(Math.abs(Number(tx.reimbursement_unallocated ?? 0)), tx.currency, locale)
    return (
      <p className="text-[10px] text-muted-foreground tabular-nums">
        {t('reimbursements.rowLeft', { amount: ltrAmount(mask(left)) })}
      </p>
    )
  }
  return null
}
