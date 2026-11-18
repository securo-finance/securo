import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { RecurringForm, type RecurringInitialValues } from '@/pages/recurring'
import { recurring as recurringApi } from '@/lib/api'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { Category, CategoryGroup, RecurringTransaction, Transaction } from '@/types'

/** Everything a bill needs that the transaction already says (issue #429).
 *
 * The description is what `recurring_match_service` compares a later charge
 * against, so the transaction's own wording goes in — not the payee alone.
 * The amount is stored unsigned, with the direction kept in `type`.
 */
function recurringInitialValues(tx: Transaction): RecurringInitialValues {
  const day = Number(tx.date.slice(8, 10))
  return {
    description: tx.description || tx.payee_name || tx.payee || '',
    amount: Math.abs(Number(tx.amount)),
    currency: tx.currency,
    type: tx.type,
    frequency: 'monthly',
    day_of_month: Number.isFinite(day) ? day : undefined,
    start_date: tx.date,
    category_id: tx.category_id ?? '',
    account_id: tx.account_id ?? '',
  }
}

/** The recurring form, opened over a transaction and filled from it. */
export function MakeRecurringDialog({
  transaction,
  accounts,
  categories,
  categoryGroups,
  onClose,
}: {
  transaction: Transaction | null
  accounts: { id: string; name: string; display_name?: string | null }[]
  categories: Category[]
  categoryGroups: CategoryGroup[]
  onClose: () => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const createMutation = useMutation({
    mutationFn: (data: Partial<RecurringTransaction>) => recurringApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      toast.success(t('recurring.created'))
      onClose()
    },
  })

  if (!transaction) return null
  const initial = recurringInitialValues(transaction)

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('recurring.makeFromTransaction')}</DialogTitle>
        </DialogHeader>
        <RecurringForm
          key={`from-${transaction.id}`}
          recurring={null}
          initial={initial}
          categories={categories}
          categoryGroups={categoryGroups}
          currentCategory={categories.find((category) => category.id === initial.category_id)}
          accounts={accounts}
          onSave={(data) => createMutation.mutate(data)}
          onCancel={onClose}
          loading={createMutation.isPending}
        />
      </DialogContent>
    </Dialog>
  )
}
