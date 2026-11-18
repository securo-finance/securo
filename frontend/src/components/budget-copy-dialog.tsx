import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CalendarIcon } from 'lucide-react'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { monthLabel, shiftMonth } from '@/lib/month-utils'
import { formatCurrency, parseAmountInput } from '@/lib/format'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { extractApiError } from '@/lib/api-errors'
import { budgets as budgetsApi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { MonthPicker } from '@/components/ui/monthpicker'
import type { Budget } from '@/types'

/** The smallest and largest adjustment the copy endpoint accepts. */
const MIN_ADJUSTMENT = -100
const MAX_ADJUSTMENT = 1000

interface BudgetCopyDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Target month as `"YYYY-MM"`. */
  targetMonth: string
  /** Budgets the target month already resolves to, for the missing-only preview. */
  targetBudgets: Budget[]
}

function capitalize(label: string) {
  return label.replace(/^\p{L}/u, (letter) => letter.toLocaleUpperCase())
}

/**
 * Copies the budgets of an earlier month into the selected one.
 *
 * The source is read with the same endpoint the page uses, so the preview
 * counts the categories the copy endpoint would resolve (month overrides plus
 * inherited recurring defaults) and leaves out the ones the target month
 * already has when the user keeps existing budgets.
 */
export function BudgetCopyDialog({ open, onOpenChange, targetMonth, targetBudgets }: BudgetCopyDialogProps) {
  const { t, i18n } = useTranslation()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const uiLocale = i18n.resolvedLanguage ?? i18n.language
  const dateFnsLocale = resolveDateFnsLocale(uiLocale)
  const currency = user?.preferences?.currency_display ?? 'USD'

  const previousMonth = shiftMonth(targetMonth, -1)
  // The page remounts this dialog on every opening (keyed by month and open
  // state), so these defaults only ever apply to a fresh dialog.
  const [sourceMonth, setSourceMonth] = useState(previousMonth)
  const [overwriteExisting, setOverwriteExisting] = useState(false)
  const [adjustment, setAdjustment] = useState('')
  const [monthPickerOpen, setMonthPickerOpen] = useState(false)

  const sourceParam = `${sourceMonth}-01`
  const { data: sourceBudgets, isPending: sourcePending, isError: sourceError } = useQuery({
    queryKey: ['budgets', sourceParam],
    queryFn: () => budgetsApi.list(sourceParam),
    enabled: open,
  })

  const parsedAdjustment = adjustment.trim() === '' ? 0 : parseAmountInput(adjustment, locale)
  const adjustmentValid = parsedAdjustment !== null && parsedAdjustment >= MIN_ADJUSTMENT && parsedAdjustment <= MAX_ADJUSTMENT

  const targetCategoryIds = useMemo(
    () => new Set(targetBudgets.map((budget) => budget.category_id)),
    [targetBudgets],
  )
  const rowsToCopy = useMemo(
    () => (sourceBudgets ?? []).filter((budget) => overwriteExisting || !targetCategoryIds.has(budget.category_id)),
    [sourceBudgets, overwriteExisting, targetCategoryIds],
  )
  const adjustmentPercent = adjustmentValid ? (parsedAdjustment ?? 0) : 0
  const copiedTotal = rowsToCopy.reduce((sum, budget) => sum + Number(budget.amount) * (1 + adjustmentPercent / 100), 0)

  const copyMutation = useMutation({
    mutationFn: () => budgetsApi.copyMonth({
      source_month: sourceParam,
      target_month: `${targetMonth}-01`,
      adjustment_percentage: parsedAdjustment ?? 0,
      overwrite_existing: overwriteExisting,
    }),
    onSuccess: (copied) => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      toast.success(copied.length > 0 ? t('budgets.copySuccess', { count: copied.length }) : t('budgets.copyNothing'))
      onOpenChange(false)
    },
    onError: (error: unknown) => toast.error(extractApiError(error, t('budgets.copyError'))),
  })

  const sameMonth = sourceMonth === targetMonth
  const canSubmit = !sameMonth && !sourcePending && !sourceError && rowsToCopy.length > 0 && adjustmentValid && !copyMutation.isPending

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!copyMutation.isPending) onOpenChange(next) }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('budgets.copyTitle')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {t('budgets.copyDescription', { month: capitalize(monthLabel(targetMonth, uiLocale)) })}
          </p>

          <div className="space-y-1">
            <Label htmlFor="budget-copy-source">{t('budgets.copySource')}</Label>
            <Popover open={monthPickerOpen} onOpenChange={setMonthPickerOpen}>
              <PopoverTrigger asChild>
                <button
                  id="budget-copy-source"
                  type="button"
                  className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground transition-all hover:bg-muted/50"
                >
                  <CalendarIcon className="size-3.5 text-muted-foreground" />
                  {capitalize(monthLabel(sourceMonth, uiLocale))}
                </button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-auto p-0">
                <MonthPicker
                  locale={dateFnsLocale}
                  selectedMonth={new Date(`${sourceMonth}-01T00:00:00`)}
                  // Only months before the target make sense as a source.
                  maxDate={new Date(`${previousMonth}-01T00:00:00`)}
                  onMonthSelect={(date) => {
                    if (!date) return
                    setSourceMonth(`${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`)
                    setMonthPickerOpen(false)
                  }}
                />
              </PopoverContent>
            </Popover>
          </div>

          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-medium text-foreground">{t('budgets.copyExisting')}</legend>
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="radio"
                name="budget-copy-strategy"
                className="mt-0.5"
                checked={!overwriteExisting}
                onChange={() => setOverwriteExisting(false)}
              />
              <span className="text-sm text-foreground">{t('budgets.copyMissing')}</span>
            </label>
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="radio"
                name="budget-copy-strategy"
                className="mt-0.5"
                checked={overwriteExisting}
                onChange={() => setOverwriteExisting(true)}
              />
              <span className="text-sm text-foreground">{t('budgets.copyOverwrite')}</span>
            </label>
          </fieldset>

          <div className="space-y-1">
            <Label htmlFor="budget-copy-adjustment">{t('budgets.copyAdjustment')}</Label>
            <Input
              id="budget-copy-adjustment"
              type="text"
              inputMode="decimal"
              placeholder="0"
              value={adjustment}
              aria-invalid={!adjustmentValid}
              aria-describedby="budget-copy-adjustment-hint"
              onChange={(event) => setAdjustment(event.target.value)}
            />
            <p id="budget-copy-adjustment-hint" className="text-xs text-muted-foreground">{t('budgets.copyAdjustmentHint')}</p>
          </div>

          <div className="rounded-lg border border-border bg-muted/20 px-3 py-2 text-sm tabular-nums">
            {sourcePending ? (
              <p className="text-muted-foreground">{t('common.loading')}</p>
            ) : sourceError ? (
              <p role="alert" className="text-destructive">{t('budgets.copyPreviewError')}</p>
            ) : rowsToCopy.length === 0 ? (
              <p className="text-muted-foreground">{t('budgets.copyPreviewEmpty')}</p>
            ) : (
              <>
                <p className="text-foreground">{t('budgets.copyPreviewCount', { count: rowsToCopy.length })}</p>
                <p className="text-muted-foreground">{t('budgets.copyPreviewTotal', { amount: mask(formatCurrency(copiedTotal, currency, locale)) })}</p>
              </>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={copyMutation.isPending}>
            {t('common.cancel')}
          </Button>
          <Button type="button" disabled={!canSubmit} onClick={() => copyMutation.mutate()}>
            {t('budgets.copyConfirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
