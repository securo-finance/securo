import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { debts as debtsApi } from '@/lib/api'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Debt, DebtPlan, DebtInstallment, DebtStrategyMethod } from '@/types'
import { ChevronDown, ChevronRight, HandCoins, Plus, Trash2 } from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { formatCurrency } from '@/lib/format'

const SELECT_CLASS = 'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}
function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm font-semibold text-foreground">{title}</p>
      {action}
    </div>
  )
}

function StatusBadge({ status, t }: { status: string; t: (key: string) => string }) {
  const config: Record<string, { bg: string; text: string; key: string }> = {
    active: { bg: 'bg-emerald-100 dark:bg-emerald-500/20', text: 'text-emerald-700 dark:text-emerald-400', key: 'debts.statusActive' },
    negotiating: { bg: 'bg-amber-100 dark:bg-amber-500/20', text: 'text-amber-700 dark:text-amber-400', key: 'debts.statusNegotiating' },
    paid_off: { bg: 'bg-blue-100 dark:bg-blue-500/20', text: 'text-blue-700 dark:text-blue-400', key: 'debts.statusPaidOff' },
    defaulted: { bg: 'bg-rose-100 dark:bg-rose-500/20', text: 'text-rose-700 dark:text-rose-400', key: 'debts.statusDefaulted' },
  }
  const c = config[status] ?? config.active
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold ${c.bg} ${c.text}`}>
      {t(c.key)}
    </span>
  )
}

function activePlanOf(debt: Debt): DebtPlan | undefined {
  return debt.plans.find((p) => p.status === 'active')
}

function DebtCard({ debt, expanded, onToggle, canWrite, t, locale, mask }: {
  debt: Debt
  expanded: boolean
  onToggle: () => void
  canWrite: boolean
  t: (key: string, opts?: Record<string, unknown>) => string
  locale: string
  mask: (s: string) => string
}) {
  const queryClient = useQueryClient()
  const [planDialogOpen, setPlanDialogOpen] = useState(false)
  const activePlan = activePlanOf(debt)

  const createPlanMutation = useMutation({
    mutationFn: (data: Partial<DebtPlan> & { activate?: boolean }) => debtsApi.createPlan(debt.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['debts'] })
      queryClient.invalidateQueries({ queryKey: ['debts-payoff-projection'] })
      setPlanDialogOpen(false)
      toast.success(t('debts.planCreated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const payMutation = useMutation({
    mutationFn: (installmentId: string) =>
      debtsApi.payInstallment(installmentId, { paid_date: new Date().toISOString().slice(0, 10) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['debts'] })
      queryClient.invalidateQueries({ queryKey: ['debts-payoff-projection'] })
      toast.success(t('debts.installmentPaid'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteMutation = useMutation({
    mutationFn: () => debtsApi.delete(debt.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['debts'] })
      toast.success(t('debts.deleted'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const paidCount = activePlan?.installments.filter((i) => i.status === 'paid').length ?? 0
  const totalCount = activePlan?.installments.length ?? 0

  return (
    <div className="px-4 sm:px-5 py-4">
      <div className="flex items-start gap-3">
        <button onClick={onToggle} className="mt-1 text-muted-foreground shrink-0">
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className="text-sm font-semibold text-foreground truncate">{debt.creditor_name}</span>
            <StatusBadge status={debt.status} t={t} />
            <span className="text-[10px] font-medium text-muted-foreground">{t(`debts.kind_${debt.kind}`)}</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className="tabular-nums font-medium text-foreground">
              {mask(formatCurrency(debt.current_balance, debt.currency, locale))}
              {' / '}
              {mask(formatCurrency(debt.original_principal, debt.currency, locale))}
            </span>
            {activePlan && (
              <span>
                {t('debts.installmentAmount')}: {mask(formatCurrency(activePlan.installment_amount, debt.currency, locale))}
                {' · '}
                {paidCount}/{totalCount}
              </span>
            )}
            {!activePlan && <span>{t('debts.noActivePlan')}</span>}
          </div>
        </div>
        {canWrite && (
          <button
            className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-colors shrink-0"
            onClick={() => deleteMutation.mutate()}
            title={t('common.delete')}
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>

      {expanded && (
        <div className="mt-4 ml-7 space-y-4">
          {activePlan ? (
            <div>
              <p className="text-xs font-semibold text-foreground mb-2">{t('debts.installments')}</p>
              <div className="space-y-1">
                {activePlan.installments.map((installment: DebtInstallment) => (
                  <div key={installment.id} className="flex items-center justify-between text-xs px-2 py-1.5 rounded-md bg-muted/40">
                    <span className="text-muted-foreground">
                      #{installment.installment_number} · {installment.due_date}
                    </span>
                    <span className="tabular-nums">{mask(formatCurrency(installment.amount, debt.currency, locale))}</span>
                    {installment.status === 'paid' ? (
                      <span className="text-emerald-600 dark:text-emerald-400 font-medium">{t('debts.installmentStatusPaid')}</span>
                    ) : canWrite ? (
                      <Button size="sm" variant="outline" className="h-6 px-2 text-[11px]" onClick={() => payMutation.mutate(installment.id)}>
                        {t('debts.markPaid')}
                      </Button>
                    ) : (
                      <span className="text-muted-foreground">{t('debts.installmentStatusPending')}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">{t('debts.noActivePlan')}</p>
          )}

          {debt.plans.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-foreground mb-2">{t('debts.plans')}</p>
              <div className="space-y-1">
                {debt.plans.map((plan) => (
                  <div key={plan.id} className="flex items-center justify-between text-xs px-2 py-1.5 rounded-md bg-muted/40">
                    <span>{t(`debts.planKind_${plan.kind}`)}</span>
                    <span className="tabular-nums text-muted-foreground">
                      {mask(formatCurrency(plan.installment_amount, debt.currency, locale))} · {plan.num_installments}x · {plan.interest_rate}%
                    </span>
                    <span className="font-medium">{t(`debts.planStatus_${plan.status}`)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {canWrite && (
            <Button size="sm" variant="outline" className="gap-1.5 h-8" onClick={() => setPlanDialogOpen(true)}>
              <Plus size={13} /> {t('debts.newPlan')}
            </Button>
          )}
        </div>
      )}

      <Dialog open={planDialogOpen} onOpenChange={setPlanDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('debts.newPlan')}</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              const fd = new FormData(e.currentTarget)
              createPlanMutation.mutate({
                kind: fd.get('kind') as DebtPlan['kind'],
                collection_mode: fd.get('collection_mode') as DebtPlan['collection_mode'],
                interest_rate: parseFloat((fd.get('interest_rate') as string) || '0'),
                installment_amount: parseFloat(fd.get('installment_amount') as string),
                num_installments: parseInt(fd.get('num_installments') as string, 10),
                first_due_date: fd.get('first_due_date') as string,
                frequency: fd.get('frequency') as DebtPlan['frequency'],
                activate: fd.get('activate') === 'on',
              })
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label>{t('debts.planKind')}</Label>
              <select name="kind" className={SELECT_CLASS} defaultValue="simulation">
                <option value="original_contract">{t('debts.planKind_original_contract')}</option>
                <option value="renegotiated">{t('debts.planKind_renegotiated')}</option>
                <option value="simulation">{t('debts.planKind_simulation')}</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label>{t('debts.collectionMode')}</Label>
              <select name="collection_mode" className={SELECT_CLASS} defaultValue="manual">
                <option value="manual">{t('debts.collectionMode_manual')}</option>
                <option value="payroll_deduction">{t('debts.collectionMode_payroll_deduction')}</option>
              </select>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t('debts.interestRate')}</Label>
                <Input name="interest_rate" type="number" step="0.0001" defaultValue="0" />
              </div>
              <div className="space-y-2">
                <Label>{t('debts.installmentAmount')}</Label>
                <Input name="installment_amount" type="number" step="0.01" required />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t('debts.numInstallments')}</Label>
                <Input name="num_installments" type="number" min="1" max="360" required />
              </div>
              <div className="space-y-2">
                <Label>{t('debts.frequency')}</Label>
                <select name="frequency" className={SELECT_CLASS} defaultValue="monthly">
                  <option value="weekly">{t('debts.frequency_weekly')}</option>
                  <option value="monthly">{t('debts.frequency_monthly')}</option>
                  <option value="quarterly">{t('debts.frequency_quarterly')}</option>
                  <option value="yearly">{t('debts.frequency_yearly')}</option>
                </select>
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t('debts.firstDueDate')}</Label>
              <Input name="first_due_date" type="date" required />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" name="activate" className="rounded border-border" />
              {t('debts.activateNow')}
            </label>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setPlanDialogOpen(false)}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={createPlanMutation.isPending}>
                {t('common.save')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default function DebtsPage() {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [openedDate, setOpenedDate] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data: debtsList } = useQuery({
    queryKey: ['debts'],
    queryFn: () => debtsApi.list(),
  })

  const { data: strategySetting } = useQuery({
    queryKey: ['debts-strategy-setting'],
    queryFn: () => debtsApi.getStrategySetting(),
  })

  const { data: projection } = useQuery({
    queryKey: ['debts-payoff-projection'],
    queryFn: () => debtsApi.payoffProjection(),
  })

  const createMutation = useMutation({
    mutationFn: (data: Partial<Debt>) => debtsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['debts'] })
      setDialogOpen(false)
      toast.success(t('debts.created'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const strategyMutation = useMutation({
    mutationFn: (data: { method?: DebtStrategyMethod; extra_monthly_amount?: number }) =>
      debtsApi.updateStrategySetting(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['debts-strategy-setting'] })
      queryClient.invalidateQueries({ queryKey: ['debts-payoff-projection'] })
      toast.success(t('debts.strategySaved'))
    },
    onError: () => toast.error(t('common.error')),
  })

  return (
    <div>
      <PageHeader section={t('debts.title')} title={t('debts.title')} />

      <div className="space-y-6">
        <SectionCard>
          <SectionHeader
            title={t('debts.title')}
            action={
              canWrite ? (
                <Button size="sm" className="gap-1.5 h-8" onClick={() => { setOpenedDate(''); setDialogOpen(true) }}>
                  <Plus size={13} /> {t('debts.add')}
                </Button>
              ) : undefined
            }
          />
          {debtsList && debtsList.length > 0 ? (
            <div className="divide-y divide-border">
              {debtsList.map((debt) => (
                <DebtCard
                  key={debt.id}
                  debt={debt}
                  expanded={expandedId === debt.id}
                  onToggle={() => setExpandedId(expandedId === debt.id ? null : debt.id)}
                  canWrite={canWrite}
                  t={t}
                  locale={locale}
                  mask={mask}
                />
              ))}
            </div>
          ) : (
            <div className="text-center py-10">
              <HandCoins size={28} className="mx-auto text-muted-foreground/50 mb-2" />
              <p className="text-sm text-muted-foreground">{t('debts.empty')}</p>
            </div>
          )}
        </SectionCard>

        <SectionCard>
          <SectionHeader title={t('debts.payoffStrategy')} />
          <div className="p-4 sm:p-5 space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t('debts.strategyMethod')}</Label>
                <select
                  className={SELECT_CLASS}
                  value={strategySetting?.method ?? 'avalanche'}
                  disabled={!canWrite}
                  onChange={(e) => strategyMutation.mutate({ method: e.target.value as DebtStrategyMethod })}
                >
                  <option value="avalanche">{t('debts.strategyAvalanche')}</option>
                  <option value="snowball">{t('debts.strategySnowball')}</option>
                </select>
              </div>
              <div className="space-y-2">
                <Label>{t('debts.extraMonthlyAmount')}</Label>
                <Input
                  type="number"
                  step="0.01"
                  defaultValue={strategySetting?.extra_monthly_amount ?? 0}
                  disabled={!canWrite}
                  onBlur={(e) => strategyMutation.mutate({ extra_monthly_amount: parseFloat(e.target.value || '0') })}
                />
              </div>
            </div>

            {projection && projection.order.length > 0 ? (
              <div className="space-y-1">
                {projection.order.map((entry, index) => (
                  <div key={entry.debt_id} className="flex items-center justify-between text-xs px-2 py-1.5 rounded-md bg-muted/40">
                    <span className="font-medium text-foreground">#{index + 1} {entry.creditor_name}</span>
                    <span className="tabular-nums text-muted-foreground">
                      {entry.months_to_payoff != null
                        ? t('debts.monthsToPayoff', { count: entry.months_to_payoff })
                        : t('debts.noProjection')}
                    </span>
                    <span className="tabular-nums text-muted-foreground">
                      {t('debts.totalInterestRemaining')}: {mask(formatCurrency(entry.total_interest_remaining, userCurrency, locale))}
                    </span>
                  </div>
                ))}
                {projection.overall_payoff_date && (
                  <p className="text-xs text-muted-foreground pt-2">
                    {t('debts.overallPayoffDate', { date: projection.overall_payoff_date })}
                  </p>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">{t('debts.noActiveDebts')}</p>
            )}
          </div>
        </SectionCard>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('debts.add')}</DialogTitle>
          </DialogHeader>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              const fd = new FormData(e.currentTarget)
              createMutation.mutate({
                kind: fd.get('kind') as Debt['kind'],
                creditor_name: fd.get('creditor_name') as string,
                original_principal: parseFloat(fd.get('original_principal') as string),
                current_balance: parseFloat(fd.get('original_principal') as string),
                currency: (fd.get('currency') as string) || userCurrency,
                opened_date: openedDate,
                notes: (fd.get('notes') as string) || null,
              } as Partial<Debt>)
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label>{t('debts.kind')}</Label>
              <select name="kind" className={SELECT_CLASS} defaultValue="loan">
                <option value="loan">{t('debts.kind_loan')}</option>
                <option value="payroll_loan">{t('debts.kind_payroll_loan')}</option>
                <option value="credit_card_overdue">{t('debts.kind_credit_card_overdue')}</option>
                <option value="other">{t('debts.kind_other')}</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label>{t('debts.creditorName')}</Label>
              <Input name="creditor_name" required />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t('debts.originalPrincipal')}</Label>
                <Input name="original_principal" type="number" step="0.01" required />
              </div>
              <div className="space-y-2">
                <Label>{t('debts.currency')}</Label>
                <Input name="currency" defaultValue={userCurrency} maxLength={3} />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t('debts.openedDate')}</Label>
              <DatePickerInput value={openedDate} onChange={setOpenedDate} className="w-full justify-start" />
            </div>
            <div className="space-y-2">
              <Label>{t('debts.notes')}</Label>
              <Input name="notes" />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={createMutation.isPending}>
                {t('common.save')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
