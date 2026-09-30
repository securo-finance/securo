import { useQuery } from '@tanstack/react-query'
import { TransactionDialog } from '@/components/transaction-dialog'
import { useCreateTransaction } from '@/hooks/use-create-transaction'
import { accounts as accountsApi, categories as categoriesApi, categoryGroups as categoryGroupsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'

/**
 * Create-transaction dialog reachable from anywhere in the app (the "+" next
 * to Transactions in the sidebar). Loaded lazily by the layout, and it only
 * fetches its lists while open, so pages that never use it pay nothing.
 */
export default function QuickAddTransaction({ open, onClose }: { open: boolean; onClose: () => void }) {
  const {
    mutation: createMutation,
    create: createTransaction,
    duplicateDraft,
    setDuplicateDraft,
    formResetKey,
  } = useCreateTransaction({ onDone: onClose })

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
    enabled: open,
  })
  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
    enabled: open,
  })
  const { data: categoryGroupsList } = useQuery({
    queryKey: ['categoryGroups'],
    queryFn: categoryGroupsApi.list,
    enabled: open,
  })

  return (
    <TransactionDialog
      open={open}
      onClose={() => {
        onClose()
        setDuplicateDraft(null)
        createMutation.reset()
      }}
      transaction={null}
      duplicateDraft={duplicateDraft}
      formResetKey={formResetKey}
      categories={categoriesList ?? []}
      categoryGroups={categoryGroupsList ?? []}
      accounts={(accountsList ?? []).filter(a => !a.is_closed)}
      onSave={(data, recurringData, installmentData, pendingFiles, action) => {
        createTransaction(data, recurringData, installmentData, pendingFiles, action)
      }}
      loading={createMutation.isPending}
      error={createMutation.error ? extractApiError(createMutation.error) : null}
    />
  )
}
