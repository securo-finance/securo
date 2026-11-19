import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CalendarIcon } from 'lucide-react'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { monthLabel, shiftMonth } from '@/lib/month-utils'
import { formatCurrency, parseAmountInput } from '@/lib/format'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { extractApiError } from '@/lib/api-errors'
import { budgets as budgetsApi } from '@/lib/api'
import { budgetDisplayAmount } from '@/lib/budget-settings-utils'
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
/** `_MAX_BUDGET_AMOUNT` in the service, in cents: a copy at or above it is rejected. */
const MAX_BUDGET_CENTS = 1e15

interface BudgetCopyPayload {
  source_month: string
  target_month: string
  adjustment_percentage: number
  overwrite_existing: boolean
}

/** What confirming sends, captured then rather than when the request runs. */
interface BudgetCopyRequest {
  payload: BudgetCopyPayload
  workspaceId: string | null
}

interface BudgetCopyDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Target month as `"YYYY-MM"`. */
  targetMonth: string
  /** Budgets the target month already resolves to, for the missing-only preview. */
  targetBudgets: Budget[]
  /** True while the page is refetching the target month: its rows are stale. */
  targetRefreshing: boolean
}

function capitalize(label: string) {
  return label.replace(/^\p{L}/u, (letter) => letter.toLocaleUpperCase())
}

/** Cents of a budget amount, which the API stores with two decimals. */
function amountToCents(amount: number | string): number {
  return Math.round(Number(amount) * 100)
}

/** `value` as an exact fraction, read from the shortest decimal text it has. */
function toFraction(value: number): { numerator: bigint; denominator: bigint } {
  const text = Math.abs(value).toString()
  const [mantissa, exponentText] = text.split('e')
  const exponent = exponentText ? Number(exponentText) : 0
  const [whole, fraction = ''] = mantissa.split('.')
  let numerator = BigInt(`${whole}${fraction}`)
  let denominator = 10n ** BigInt(fraction.length)
  if (exponent > 0) numerator *= 10n ** BigInt(exponent)
  if (exponent < 0) denominator *= 10n ** BigInt(-exponent)
  return { numerator: value < 0 ? -numerator : numerator, denominator }
}

/**
 * `amount` in cents with the percentage adjustment applied, rounded to cents
 * the way the copy endpoint rounds it before saving (Decimal ROUND_HALF_UP:
 * on a tie, away from zero). The arithmetic is exact, so a value just below
 * the tie is never rounded up into one, and summing these keeps the preview
 * equal to what the copy writes.
 */
function adjustedCents(amount: number | string, adjustmentPercent: number): number {
  const cents = BigInt(amountToCents(amount))
  if (adjustmentPercent === 0) return Number(cents)
  // multiplier = 1 + adjustmentPercent / 100, as the fraction below.
  const { numerator, denominator } = toFraction(adjustmentPercent)
  const scale = 100n * denominator
  const scaled = cents * (scale + numerator)
  const negative = scaled < 0n
  const magnitude = negative ? -scaled : scaled
  let rounded = magnitude / scale
  if ((magnitude % scale) * 2n >= scale) rounded += 1n
  return Number(negative ? -rounded : rounded)
}

/**
 * Copies the budgets of an earlier month into the selected one.
 *
 * The source is read with the same endpoint the page uses, so the preview
 * counts the categories the copy endpoint would resolve (month overrides plus
 * inherited recurring defaults) and leaves out the ones the target month
 * already has when the user keeps existing budgets.
 */
export function BudgetCopyDialog({ open, onOpenChange, targetMonth, targetBudgets, targetRefreshing }: BudgetCopyDialogProps) {
  const { t, i18n } = useTranslation()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { current: workspace } = useWorkspace()
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  // The source read and the copy are pinned to this workspace, so a switch in
  // another tab cannot send one workspace's amounts into another.
  const workspaceId = workspace?.id ?? null
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
  const {
    data: sourceBudgets,
    isPending: sourcePending,
    isFetching: sourceFetching,
    isError: sourceError,
  } = useQuery({
    queryKey: ['budgets', sourceParam],
    queryFn: () => budgetsApi.list(sourceParam, workspaceId),
    enabled: open,
    // The app keeps queries fresh for five minutes and this dialog is
    // remounted on every opening, so without this a source changed on another
    // device would be previewed from the cache while the copy reads the
    // current amounts.
    refetchOnMount: 'always',
  })

  const parsedAdjustment = adjustment.trim() === '' ? 0 : parseAmountInput(adjustment, locale)
  const adjustmentValid = parsedAdjustment !== null && parsedAdjustment >= MIN_ADJUSTMENT && parsedAdjustment <= MAX_ADJUSTMENT
  const adjustmentPercent = adjustmentValid ? (parsedAdjustment ?? 0) : 0

  // Same rule as the service: skip a category the target month already
  // resolves to, and in replace mode also one already at the copied amount.
  // The preview therefore counts what the copy writes, not every source row.
  const preview = useMemo(() => {
    const targetCents = new Map(
      targetBudgets.map((budget) => [budget.category_id, adjustedCents(budget.amount, 0)]),
    )
    // Two people can each hold an override for the same category; the service
    // keeps one entry per category, so the preview does too instead of
    // counting both rows.
    const sourceByCategory = new Map<string, Budget>()
    for (const budget of sourceBudgets ?? []) sourceByCategory.set(budget.category_id, budget)

    // Each copied limit, in the display currency for the total.
    const rows: number[] = []
    let overLimit = false
    for (const [categoryId, budget] of sourceByCategory) {
      const copiedCents = adjustedCents(budget.amount, adjustmentPercent)
      // The service rejects the whole request over the limit, even for a
      // category it would otherwise skip.
      if (copiedCents >= MAX_BUDGET_CENTS) overLimit = true
      const existingCents = targetCents.get(categoryId)
      if (existingCents !== undefined && (!overwriteExisting || existingCents === copiedCents)) continue
      rows.push(adjustedCents(budgetDisplayAmount(budget), adjustmentPercent))
    }
    return { rows, overLimit }
  }, [sourceBudgets, targetBudgets, overwriteExisting, adjustmentPercent])
  const rowsToCopy = preview.rows
  const copiedTotal = rowsToCopy.reduce((sum, cents) => sum + cents, 0) / 100

  const copyMutation = useMutation({
    // The options and the workspace come from the confirmation, not from the
    // form when the request finally runs: a mutation queued while offline
    // resumes with whatever the fields say by then, which may not be what the
    // user confirmed.
    mutationFn: ({ payload, workspaceId: targetWorkspaceId }: BudgetCopyRequest) =>
      budgetsApi.copyMonth(payload, targetWorkspaceId),
    onSuccess: (copied) => {
      toast.success(copied.length > 0 ? t('budgets.copySuccess', { count: copied.length }) : t('budgets.copyNothing'))
      onOpenChange(false)
    },
    onError: (error: unknown) => toast.error(extractApiError(error, t('budgets.copyError'))),
    // The service may commit before failing while building the response, so
    // refresh the page's data even when the copy reports an error.
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['budgets'] }),
  })

  const sameMonth = sourceMonth === targetMonth
  // A refresh keeps the cached rows on screen; wait for both queries, since
  // the copy itself reads the source month on the server and the target rows
  // decide what it skips.
  const canSubmit = !sameMonth && !sourceFetching && !sourceError && !targetRefreshing
    && !preview.overLimit && rowsToCopy.length > 0 && adjustmentValid && !copyMutation.isPending

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
            ) : preview.overLimit ? (
              <p role="alert" className="text-destructive">{t('budgets.copyAmountTooLarge')}</p>
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
          <Button
            type="button"
            disabled={!canSubmit}
            onClick={() => copyMutation.mutate({
              payload: {
                source_month: sourceParam,
                target_month: `${targetMonth}-01`,
                adjustment_percentage: parsedAdjustment ?? 0,
                overwrite_existing: overwriteExisting,
              },
              workspaceId,
            })}
          >
            {t('budgets.copyConfirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
