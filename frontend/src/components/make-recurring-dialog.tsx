import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { RecurringForm, type RecurringInitialValues } from '@/pages/recurring'
import { recurring as recurringApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { isSplitByCategory } from '@/lib/category-lines'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
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
 * The amount is stored unsigned, with the direction kept in `type`, and a
 * split across categories travels with its parts.
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
    category_lines: isSplitByCategory(tx) ? tx.category_lines : undefined,
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
    mutationFn: ({ data, sourceDate }: { id: string; data: Partial<RecurringTransaction>; sourceDate: string }) =>
      recurringApi.create({
        ...data,
        // The source transaction covers the start-date occurrence only while
        // the bill still starts on its own date: moving the start date makes
        // that first occurrence one no transaction covers, so it must happen.
        skip_first: data.start_date === sourceDate,
      }),
    onSuccess: (_created, { id }) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      toast.success(t('recurring.created'))
      // A save that finishes after the user moved on to another transaction
      // must not close the one now on screen.
      if (transaction?.id === id) onClose()
    },
    onError: (error: unknown) => toast.error(extractApiError(error, t('common.error'))),
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
          currentCategory={
            categories.find((category) => category.id === initial.category_id)
            ?? transaction.category
            ?? undefined
          }
          accounts={accounts}
          onSave={(data) => createMutation.mutate({ id: transaction.id, sourceDate: transaction.date, data })}
          onCancel={onClose}
          loading={createMutation.isPending}
        />
      </DialogContent>
    </Dialog>
  )
}
