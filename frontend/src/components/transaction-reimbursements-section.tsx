import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Undo2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  ReimbursementDialog,
  type ReimbursementAccount,
  type ReimbursementDialogMode,
} from '@/components/reimbursement-dialog'
import { formatCurrency, ltrAmount } from '@/lib/format'
import { formatReimbursementNet } from '@/lib/reimbursements'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import type { Transaction } from '@/types'

/**
 * A saved row's reimbursement links in the transaction dialog: what the row
 * nets to, or what is left of it, and the picker to change them.
 */
export function TransactionReimbursementsSection({
  transaction,
  accounts,
}: {
  transaction: Transaction
  accounts: ReimbursementAccount[]
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const [mode, setMode] = useState<ReimbursementDialogMode | null>(null)

  const role = transaction.reimbursement_role
  const net = formatReimbursementNet(transaction, locale)
  const summary =
    role === 'anchor'
      ? net != null ? t('reimbursements.sectionNet', { amount: ltrAmount(net) }) : t('reimbursements.sectionLinked')
      : role === 'counterpart'
        ? t('reimbursements.sectionLeft', {
            amount: ltrAmount(
              formatCurrency(Math.abs(Number(transaction.reimbursement_unallocated ?? 0)), transaction.currency, locale),
            ),
          })
        : t('reimbursements.sectionEmpty')

  return (
    <div className="space-y-2 rounded-lg border border-teal-200 dark:border-teal-900 p-3">
      <span className="inline-flex items-center gap-2 text-sm font-medium">
        <Undo2 size={14} className="text-teal-600 dark:text-teal-400" />
        {t('reimbursements.title')}
      </span>
      <p className="text-xs text-muted-foreground tabular-nums">{summary}</p>
      <div className="flex flex-wrap gap-2">
        {role !== 'counterpart' && (
          <Button type="button" variant="outline" size="sm" onClick={() => setMode('counterparts')}>
            {role === 'anchor' ? t('reimbursements.manage') : t('reimbursements.addReimbursements')}
          </Button>
        )}
        {role !== 'anchor' && (
          <Button type="button" variant="outline" size="sm" onClick={() => setMode('anchors')}>
            {role === 'counterpart' ? t('reimbursements.manage') : t('reimbursements.linkAsReimbursement')}
          </Button>
        )}
      </div>
      <ReimbursementDialog
        open={mode != null}
        onClose={() => setMode(null)}
        transaction={transaction}
        mode={mode ?? 'counterparts'}
        accounts={accounts}
      />
    </div>
  )
}
