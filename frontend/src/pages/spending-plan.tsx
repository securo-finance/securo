import { useDeferredValue, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Pencil, Plus } from 'lucide-react'
import { spendingPlan as spendingPlanApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { DayByDay } from '@/components/spending-plan/day-by-day'
import { usePlanFormat } from '@/components/spending-plan/plan-format'
import { PlanEditorDialog } from '@/components/spending-plan/plan-editor-dialog'
import { PlanHero } from '@/components/spending-plan/plan-hero'
import { invalidatePlanQueries } from '@/components/spending-plan/plan-queries'
import { PlanReceipt } from '@/components/spending-plan/plan-receipt'
import { asPurchase, useWhatIfItems } from '@/components/spending-plan/use-what-if-items'
import { useWorkspace } from '@/contexts/workspace-context'
import type { SpendingPlan } from '@/types'

export default function SpendingPlanPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { canWrite, current: workspace } = useWorkspace()

  const { data: plan, isLoading: planLoading } = useQuery({
    queryKey: ['spending-plan'],
    queryFn: spendingPlanApi.get,
  })
  const savedAllowance = plan ? Number(plan.daily_allowance) : 0
  // The slider previews an allowance without saving it until asked.
  const [draftAllowance, setDraftAllowance] = useState<number | null>(null)
  const allowance = draftAllowance ?? savedAllowance
  const [whatIfItems, setWhatIfItems] = useWhatIfItems(workspace?.id)
  const purchases = plan ? whatIfItems.filter((item) => item.enabled).map(asPurchase) : []
  // Without a draft the server walks the saved allowance.
  const previewAllowance = useDeferredValue(draftAllowance ?? undefined)
  const { data: timeline } = useQuery({
    queryKey: ['dashboard', 'spending-plan', 'simulate', previewAllowance, purchases],
    queryFn: () => spendingPlanApi.simulate(purchases, previewAllowance),
    enabled: !planLoading,
    placeholderData: (previous) => previous,
  })
  const format = usePlanFormat(timeline?.currency ?? 'BRL')

  const saveAllowance = useMutation({
    mutationFn: (current: SpendingPlan) =>
      spendingPlanApi.save({
        daily_allowance: allowance,
        balance_floor: Number(current.balance_floor),
        account_ids: current.account_ids,
        horizon_months: current.horizon_months,
      }),
    onSuccess: (saved) => {
      queryClient.setQueryData(['spending-plan'], saved)
      setDraftAllowance(null)
      invalidatePlanQueries(queryClient)
      toast.success(t('spendingPlan.allowanceSaved'))
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const [editorOpen, setEditorOpen] = useState(false)
  const editButton = canWrite ? (
    <Button
      size="sm"
      variant={plan ? 'outline' : 'default'}
      className="gap-1.5 h-8"
      onClick={() => setEditorOpen(true)}
    >
      {plan ? <Pencil size={13} /> : <Plus size={13} />}
      {plan ? t('spendingPlan.editPlan') : t('spendingPlan.createPlan')}
    </Button>
  ) : undefined

  return (
    <div>
      <PageHeader section={t('spendingPlan.title')} title={t('spendingPlan.title')} action={editButton} />

      {planLoading || !timeline ? (
        <Skeleton className="h-40 w-full rounded-xl mb-5" />
      ) : plan ? (
        <PlanHero
          timeline={timeline}
          allowance={allowance}
          savedAllowance={savedAllowance}
          onAllowanceChange={setDraftAllowance}
          onSave={() => saveAllowance.mutate(plan)}
          saving={saveAllowance.isPending}
          canWrite={canWrite}
          whatIfItems={whatIfItems}
          onWhatIfChange={setWhatIfItems}
          format={format}
        />
      ) : (
        <Card className="mb-5 flex-col gap-4 px-5 py-6 sm:flex-row sm:items-center sm:justify-between">
          <div className="max-w-2xl space-y-1">
            <p className="text-sm font-semibold text-foreground">{t('spendingPlan.noPlan')}</p>
            <p className="text-sm text-muted-foreground">{t('spendingPlan.intro')}</p>
            <p className="text-sm text-foreground">
              {t('spendingPlan.noPlanSuggestion', {
                surplus: format.money(timeline.average_daily_surplus),
                amount: format.money(timeline.safe_daily_allowance),
                date: format.shortDate(timeline.horizon_end),
              })}
            </p>
          </div>
          {editButton}
        </Card>
      )}

      {timeline && (
        <>
          <DayByDay timeline={timeline} format={format} />
          <PlanReceipt timeline={timeline} format={format} />
        </>
      )}

      {editorOpen && <PlanEditorDialog plan={plan ?? null} onClose={() => setEditorOpen(false)} />}
    </div>
  )
}
