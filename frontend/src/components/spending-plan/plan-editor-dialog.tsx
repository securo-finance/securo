import { useDeferredValue, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { accounts as accountsApi, spendingPlan as spendingPlanApi } from '@/lib/api'
import { getAccountName, sortAccountsByDisplayName } from '@/lib/account-utils'
import { extractApiError } from '@/lib/api-errors'
import { formatCurrency } from '@/lib/format'
import { coverageLabel } from '@/lib/spending-plan'
import { AccountIcon } from '@/components/account-icon'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import type { SpendingPlan, SpendingPlanInput } from '@/types'
import { invalidatePlanQueries } from './plan-queries'

const SELECT_CLASS =
  'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'
const HORIZONS = [6, 12, 18, 24, 36]

function parseAmount(value: string) {
  const parsed = Number(value.replace(',', '.'))
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0
}

function draftFrom(plan: SpendingPlan | null): SpendingPlanInput {
  return {
    daily_allowance: plan ? Number(plan.daily_allowance) : 0,
    balance_floor: plan ? Number(plan.balance_floor) : 0,
    account_ids: plan?.account_ids ?? null,
    horizon_months: plan?.horizon_months ?? 12,
  }
}

export function PlanEditorDialog({ plan, onClose }: { plan: SpendingPlan | null; onClose: () => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })
  const [draft, setDraft] = useState<SpendingPlanInput>(() => draftFrom(plan))
  const [allowanceText, setAllowanceText] = useState(() => (plan ? String(Number(plan.daily_allowance)) : ''))
  const [floorText, setFloorText] = useState(() =>
    plan && Number(plan.balance_floor) ? String(Number(plan.balance_floor)) : '',
  )

  const previewDraft = useDeferredValue(draft)
  const { data: preview } = useQuery({
    queryKey: ['dashboard', 'spending-plan', 'preview', previewDraft],
    queryFn: () => spendingPlanApi.preview(previewDraft),
    placeholderData: (previous) => previous,
  })
  const money = (value: number) => mask(formatCurrency(value, preview?.currency ?? 'BRL', locale))

  const invalidate = () => invalidatePlanQueries(queryClient)
  const save = useMutation({
    mutationFn: () => spendingPlanApi.save(draft),
    onSuccess: () => {
      invalidate()
      toast.success(t('spendingPlan.saved'))
      onClose()
    },
    onError: (error) => toast.error(extractApiError(error)),
  })
  const remove = useMutation({
    mutationFn: spendingPlanApi.remove,
    onSuccess: () => {
      invalidate()
      toast.success(t('spendingPlan.removed'))
      onClose()
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const planAccounts = useMemo(
    () => sortAccountsByDisplayName((accountsList ?? []).filter((a) => !a.is_closed && a.type !== 'credit_card')),
    [accountsList],
  )
  const isChosen = (id: string, type: string) =>
    draft.account_ids === null ? type !== 'loan' : draft.account_ids.includes(id)
  const toggleAccount = (id: string) =>
    setDraft((current) => {
      const base = current.account_ids ?? planAccounts.filter((a) => a.type !== 'loan').map((a) => a.id)
      return { ...current, account_ids: base.includes(id) ? base.filter((x) => x !== id) : [...base, id] }
    })

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{plan ? t('spendingPlan.editPlan') : t('spendingPlan.createPlan')}</DialogTitle>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="plan-allowance">{t('spendingPlan.dailyAllowance')}</Label>
            <Input
              id="plan-allowance"
              type="number"
              step="0.01"
              min="0"
              value={allowanceText}
              placeholder="0,00"
              onChange={(event) => {
                const value = event.target.value
                setAllowanceText(value)
                setDraft((current) => ({ ...current, daily_allowance: parseAmount(value) }))
              }}
              required
            />
            {preview && (
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs">
                <span className={preview.covered_until === null ? 'text-rose-500' : 'text-muted-foreground'}>
                  {coverageLabel(preview, dateLocale, t)}
                </span>
                {preview.safe_daily_allowance !== draft.daily_allowance && (
                  <button
                    type="button"
                    className="text-primary hover:underline"
                    onClick={() => {
                      const safe = preview.safe_daily_allowance
                      setAllowanceText(String(safe))
                      setDraft((current) => ({ ...current, daily_allowance: safe }))
                    }}
                  >
                    {t('spendingPlan.useSafe', { amount: money(preview.safe_daily_allowance) })}
                  </button>
                )}
              </div>
            )}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="plan-floor">{t('spendingPlan.balanceFloor')}</Label>
              <Input
                id="plan-floor"
                type="number"
                step="0.01"
                min="0"
                value={floorText}
                placeholder="0,00"
                onChange={(event) => {
                  const value = event.target.value
                  setFloorText(value)
                  setDraft((current) => ({ ...current, balance_floor: parseAmount(value) }))
                }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="plan-horizon">{t('spendingPlan.horizon')}</Label>
              <select
                id="plan-horizon"
                className={SELECT_CLASS}
                value={draft.horizon_months}
                onChange={(event) => {
                  const months = Number(event.target.value)
                  setDraft((current) => ({ ...current, horizon_months: months }))
                }}
              >
                {HORIZONS.map((months) => (
                  <option key={months} value={months}>
                    {t('spendingPlan.months', { count: months })}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <p className="text-xs text-muted-foreground -mt-2">{t('spendingPlan.balanceFloorHint')}</p>

          <div className="space-y-2">
            <Label>{t('spendingPlan.accounts')}</Label>
            <p className="text-xs text-muted-foreground">{t('spendingPlan.accountsHint')}</p>
            <div className="rounded-lg border border-border divide-y divide-border max-h-48 overflow-y-auto">
              {planAccounts.map((account) => (
                <label key={account.id} className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-muted/40">
                  <input
                    type="checkbox"
                    className="rounded border-border"
                    checked={isChosen(account.id, account.type)}
                    onChange={() => toggleAccount(account.id)}
                  />
                  <AccountIcon account={account} size="sm" />
                  <span className="text-sm text-foreground truncate">{getAccountName(account)}</span>
                </label>
              ))}
            </div>
          </div>

          <DialogFooter className="sm:justify-between gap-2">
            {plan ? (
              <Button
                type="button"
                variant="ghost"
                className="text-rose-500 hover:text-rose-600"
                onClick={() => remove.mutate()}
                disabled={remove.isPending}
              >
                {t('spendingPlan.remove')}
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={onClose}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={save.isPending}>
                {t('common.save')}
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
