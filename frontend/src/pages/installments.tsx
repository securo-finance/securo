import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { toast } from 'sonner'
import { installments as installmentsApi, accounts as accountsApi } from '@/lib/api'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { InstallmentSummaryCard } from '@/components/installment-summary-card'
import { InstallmentPurchaseCard } from '@/components/installment-purchase-card'
import { InstallmentDialog } from '@/components/installment-dialog'
import { InstallmentFilterBar } from '@/components/installment-filter-bar'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { useWorkspace } from '@/contexts/workspace-context'
import type { InstallmentPurchase, ManualInstallmentCreate } from '@/types'

export default function InstallmentsPage() {
  const { t } = useTranslation()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()

  const [statusFilter, setStatusFilter] = useState('ACTIVE')
  const [sortBy, setSortBy] = useState('date')
  const [accountId, setAccountId] = useState('all')
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<InstallmentPurchase | null>(null)
  const [showOccluded, setShowOccluded] = useState(false)

  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['installments', 'summary'],
    queryFn: () => installmentsApi.summary(),
  })

  const { data: allPurchases, isLoading: purchasesLoading } = useQuery({
    queryKey: ['installments', 'purchases', sortBy, accountId],
    queryFn: () =>
      installmentsApi.list({
        account_id: accountId === 'all' ? undefined : accountId,
        sort: sortBy,
      }),
  })

  const { data: accountsData } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const createMutation = useMutation({
    mutationFn: (data: ManualInstallmentCreate) => installmentsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['installments'] })
      invalidateFinancialQueries(queryClient)
      setDialogOpen(false)
      toast.success(t('installments.created', 'Installment created'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<ManualInstallmentCreate> }) =>
      installmentsApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['installments'] })
      invalidateFinancialQueries(queryClient)
      setDialogOpen(false)
      setEditing(null)
      toast.success(t('installments.updated', 'Installment updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const handleSubmit = (data: ManualInstallmentCreate) => {
    if (editing) {
      updateMutation.mutate({ id: editing.id, data })
    } else {
      createMutation.mutate(data)
    }
  }

  const accounts = accountsData || []

  // Compute tab counts and apply filters
  const activeCount = allPurchases?.filter((p) => p.status === 'ACTIVE').length ?? 0
  const finishedCount = allPurchases?.filter((p) => p.status === 'FINISHED').length ?? 0

  const purchases = allPurchases?.filter((p) => {
    // Status filter
    if (statusFilter === 'ACTIVE' && p.status !== 'ACTIVE') return false
    if (statusFilter === 'FINISHED' && p.status !== 'FINISHED') return false
    // Occluded filter (hide partial sync data when toggle is OFF)
    if (!showOccluded && p.has_partial_sync_data) return false
    return true
  })

  return (
    <div>
      <PageHeader
        section={t('nav.installments', 'Parcelamentos')}
        title={t('installments.title', 'Parcelamentos')}
        action={
          canWrite ? (
            <Button
              size="sm"
              className="gap-1.5 h-8"
              onClick={() => {
                setEditing(null)
                setDialogOpen(true)
              }}
            >
              <Plus size={13} /> {t('installments.add', 'Adicionar Parcelamento')}
            </Button>
          ) : undefined
        }
      />

      <div className="px-4 sm:px-6 space-y-6">
        <InstallmentSummaryCard summary={summary} isLoading={summaryLoading} />

        <InstallmentFilterBar
          statusFilter={statusFilter}
          onStatusChange={setStatusFilter}
          sortBy={sortBy}
          onSortChange={setSortBy}
          accountId={accountId}
          onAccountChange={setAccountId}
          accounts={accounts}
          activeCount={activeCount}
          finishedCount={finishedCount}
          showOccluded={showOccluded}
          onShowOccludedChange={setShowOccluded}
        />

        {purchasesLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-24 bg-muted rounded-xl animate-pulse" />
            ))}
          </div>
        ) : purchases && purchases.length > 0 ? (
          <div className="space-y-3">
            {purchases.map((purchase) => (
              <InstallmentPurchaseCard
                key={purchase.id}
                purchase={purchase}
              />
            ))}
          </div>
        ) : (
          <div className="text-center py-12 text-muted-foreground">
            {t('installments.empty', 'Nenhum parcelamento encontrado')}
          </div>
        )}
      </div>

      <InstallmentDialog
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open)
          if (!open) setEditing(null)
        }}
        onSubmit={handleSubmit}
        isLoading={createMutation.isPending || updateMutation.isPending}
        accounts={accounts}
        initialData={
          editing
            ? {
                merchant_name: editing.merchant_name,
                account_id: '',
                total_amount: editing.total_amount,
                total_installments: editing.total_installments,
                purchase_date: editing.purchase_date,
                monthly_amount: editing.installment_monthly_amount,
              }
            : undefined
        }
      />
    </div>
  )
}
