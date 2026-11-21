import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { budgets as budgetsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { incomeShare, parseBudgetDraft, savedPlannedTotal } from '@/lib/budget-settings-utils'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { Button } from '@/components/ui/button'
import type { Budget } from '@/types'

/**
 * The month's hand-typed income compared to the month's *saved* budget total.
 * The inline input answers "set"; after that the row reads income, planned,
 * the difference and the coverage — all on one line, so the successor of
 * #1338 can slot its per-row figures here without reflowing the card.
 *
 * Income and planned limits are workspace-wide, never account-scoped, so
 * nothing here uses spending or the collection filter (#1104 D3).
 */
export function BudgetIncomeStrip({ month, savedBudgets, canWrite, currency, locale, fmt }: {
  month: string
  savedBudgets: Budget[]
  canWrite: boolean
  currency: string
  locale: string
  fmt: (amount: number) => string
}) {
  const { t } = useTranslation()
  const { mask, privacyMode } = usePrivacyMode()
  const queryClient = useQueryClient()
  const errorId = useId()
  const [draft, setDraft] = useState('')
  const [focused, setFocused] = useState(false)

  const { data: income, isError: incomeLoadError } = useQuery({
    queryKey: ['budgets', 'expected-income', month],
    queryFn: () => budgetsApi.expectedIncome.get(month),
    // The value changes only through the PUT below (which invalidates the key),
    // so a remount need not refetch behind the page's own read.
    staleTime: 60_000,
  })

  const saveIncome = useMutation({
    mutationFn: (amount: string | null) => budgetsApi.expectedIncome.put({ month, amount }),
    onSuccess: () => {
      // ['budgets'] from the page invalidates the whole family; this pins the strip's own read.
      queryClient.invalidateQueries({ queryKey: ['budgets', 'expected-income', month] })
      setDraft('')
      toast.success(t('budgets.incomeSaved'))
    },
    onError: (error: unknown) => toast.error(extractApiError(error, t('common.error'))),
  })

  if (incomeLoadError) {
    return (
      <p role="alert" className="border-b border-border px-4 py-3 text-sm text-destructive sm:px-5">
        {t('budgets.incomeLoadError')}
      </p>
    )
  }

  // Nothing to compare against while the read is in flight: the rows keep their place.
  if (income === undefined) return null

  // A missing rate leaves amount_primary null: the raw amount is only
  // comparable when it is already in the display currency. Otherwise it is
  // shown in its own currency and the comparison is withheld (D5) instead of
  // being priced as if it were the display currency.
  const rawIncome = income === null ? null : Number(income.amount)
  const rawCurrency = income === null ? currency : (income.currency ?? currency)
  const rawInDisplayCurrency = rawCurrency.toUpperCase() === currency.toUpperCase()
  const incomeValue = income === null
    ? null
    : income.amount_primary !== null
      ? Number(income.amount_primary)
      : rawInDisplayCurrency ? rawIncome : null
  const incomeText = income === null
    ? ''
    : income.amount_primary !== null
      ? fmt(Number(income.amount_primary))
      : rawInDisplayCurrency
        ? fmt(rawIncome ?? 0)
        : mask(formatCurrency(rawIncome ?? 0, rawCurrency, locale))
  const planned = savedPlannedTotal(savedBudgets)
  // Signed difference and its share of the income, in minor units, so tenths
  // and floored percentages stay exact; no income means no share to divide by.
  const share = incomeShare(incomeValue, planned)
  const shareClass = share === null || share.cents === 0
    ? 'text-muted-foreground'
    : share.cents > 0
      ? 'text-emerald-700 dark:text-emerald-300'
      : 'text-rose-700 dark:text-rose-300'
  const parsed = parseBudgetDraft(draft, locale)
  const canSet = parsed !== null && parsed > 0
  const invalidDraft = parsed === null && draft.trim() !== ''

  if (income === null) {
    if (!canWrite) {
      return (
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3 sm:px-5">
          <span className="text-sm font-medium text-foreground">{t('budgets.incomeLabel')}</span>
          <span className="text-sm text-muted-foreground" aria-hidden>—</span>
        </div>
      )
    }
    return (
      <form
        className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/20 px-4 py-3 sm:px-5"
        onSubmit={(event) => {
          event.preventDefault()
          if (canSet) saveIncome.mutate(String(parsed))
        }}
      >
        <span className="text-sm font-medium text-foreground">{t('budgets.incomeLabel')}</span>
        <div className="flex w-28 items-center rounded-lg border border-border bg-card px-2.5 focus-within:ring-2 focus-within:ring-primary">
          <span className="text-xs text-muted-foreground">{formatCurrency(0, currency, locale).replace(/[\d.,\s]/g, '')}</span>
          <input
            type="text"
            inputMode="decimal"
            placeholder="0"
            aria-label={t('budgets.incomeLabel')}
            aria-invalid={invalidDraft}
            aria-describedby={invalidDraft ? errorId : undefined}
            value={privacyMode && !focused && draft ? mask(draft) : draft}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            onChange={(event) => setDraft(event.target.value)}
            className="h-8 w-full min-w-0 bg-transparent text-end text-sm font-semibold tabular-nums text-foreground outline-none placeholder:text-muted-foreground/50"
          />
        </div>
        {invalidDraft && (
          <span id={errorId} className="text-xs text-destructive">{t('budgets.invalidAmount')}</span>
        )}
        <Button size="sm" variant="outline" type="submit" disabled={!canSet || saveIncome.isPending}>
          {t('budgets.incomeSet')}
        </Button>
      </form>
    )
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-border bg-muted/20 px-4 py-3 sm:px-5">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm font-medium text-foreground">{t('budgets.incomeLabel')}</span>
        <span className="text-sm font-semibold tabular-nums text-foreground">{incomeText}</span>
        <span className="text-xs text-muted-foreground tabular-nums">{t('budgets.incomePlanned', { amount: fmt(planned) })}</span>
        {share !== null && (
          <span className={`text-xs tabular-nums ${shareClass}`}>
            {share.cents < 0
              ? share.percent !== null
                ? t('budgets.incomeShortShare', { percent: share.percent, amount: fmt(Math.abs(share.cents) / 100) })
                : t('budgets.incomeShort', { amount: fmt(Math.abs(share.cents) / 100) })
              : share.percent !== null
                ? t('budgets.incomeLeftShare', { percent: share.percent, amount: fmt(share.cents / 100) })
                : t('budgets.incomeLeft', { amount: fmt(share.cents / 100) })}
          </span>
        )}
      </div>
      {canWrite && (
        <Button size="sm" variant="ghost" disabled={saveIncome.isPending} onClick={() => saveIncome.mutate(null)}>
          {t('budgets.incomeClear')}
        </Button>
      )}
    </div>
  )
}
