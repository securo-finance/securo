import { useTranslation } from 'react-i18next'
import { ChartPie, Plus, X } from 'lucide-react'

import { CategorySelect } from '@/components/category-select'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import {
  checkLines,
  emptyRow,
  rowBasisPoints,
  rowCents,
  type CategoryLineRow,
} from '@/lib/category-lines'
import { formatAmountInput, formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { Category, CategoryGroup, CategoryLineShareType } from '@/types'

/**
 * Editor for a transaction split across several categories (#425).
 *
 * Controlled: the dialog owns the rows and the share type, so it can build
 * the save payload and gate the save button. Every part needs a category
 * and a positive share; exact parts must add up to the amount to the cent,
 * percentages to exactly 100.
 */
export function TransactionCategoryLinesSection({
  amount,
  currency,
  rows,
  shareType,
  onChange,
  onUndo,
  categories,
  groups,
  hideAmounts = false,
}: {
  amount: number
  currency: string
  rows: CategoryLineRow[]
  shareType: CategoryLineShareType
  onChange: (rows: CategoryLineRow[], shareType: CategoryLineShareType) => void
  onUndo: () => void
  categories: Category[]
  groups: CategoryGroup[]
  hideAmounts?: boolean
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const total = Math.abs(amount)
  const check = checkLines(rows, shareType, total, locale)
  // Ignored categories take the whole row out of the balance and hidden ones
  // are out of sight; neither can hold one part of a transaction.
  const usable = categories.filter((c) => !c.is_ignored && !c.is_hidden)
  const usableGroups = groups.map((group) => ({
    ...group,
    categories: (group.categories ?? []).filter((c) => !c.is_ignored && !c.is_hidden),
  }))

  const update = (key: string, patch: Partial<CategoryLineRow>) =>
    onChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)), shareType)

  const remove = (key: string) => onChange(rows.filter((r) => r.key !== key), shareType)

  const add = () => onChange([...rows, emptyRow()], shareType)

  // Switching modes carries the current shares over, so a user who typed
  // amounts and wants to see percentages does not start again.
  const switchMode = (next: CategoryLineShareType) => {
    if (next === shareType) return
    const converted = rows.map((row) => {
      if (next === 'percent') {
        const cents = rowCents(row.amount, locale)
        const pct = cents && total ? (cents / (total * 100)) * 100 : null
        return { ...row, percent: pct != null ? String(Math.round(pct * 100) / 100) : '' }
      }
      const bp = rowBasisPoints(row.percent)
      const value = bp && total ? Math.round(total * bp) / 10000 : null
      return { ...row, amount: value != null ? formatAmountInput(value, locale) : '' }
    })
    // A split that was fully allocated stays so: rounding each share can
    // leave a cent or a hundredth over or short, which goes to the last part.
    const last = converted[converted.length - 1]
    if (check.valid && last) {
      const after = checkLines(converted, next, total, locale)
      if (after.remaining !== 0 && after.reason !== 'invalidValue') {
        if (next === 'percent') {
          const bp = (rowBasisPoints(last.percent) ?? 0) + after.remaining
          converted[converted.length - 1] = { ...last, percent: String(bp / 100) }
        } else {
          const cents = (rowCents(last.amount, locale) ?? 0) + after.remaining
          converted[converted.length - 1] = { ...last, amount: formatAmountInput(cents / 100, locale) }
        }
      }
    }
    onChange(converted, next)
  }

  // Put what is left into the last part without a share, or the last part.
  const fillRemaining = () => {
    if (check.remaining <= 0) return
    const target = [...rows].reverse().find((r) =>
      shareType === 'exact' ? !rowCents(r.amount, locale) : !rowBasisPoints(r.percent),
    ) ?? rows[rows.length - 1]
    if (!target) return
    if (shareType === 'exact') {
      const current = rowCents(target.amount, locale) ?? 0
      update(target.key, { amount: formatAmountInput((current + check.remaining) / 100, locale) })
    } else {
      const current = rowBasisPoints(target.percent) ?? 0
      update(target.key, { percent: String((current + check.remaining) / 100) })
    }
  }

  const remainingLabel =
    shareType === 'exact'
      ? formatCurrency(Math.abs(check.remaining) / 100, currency, locale)
      : `${Math.abs(check.remaining) / 100}%`

  return (
    <div className="space-y-3 rounded-lg border border-violet-200 dark:border-violet-900 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-2 text-sm font-medium">
          <ChartPie size={14} className="text-violet-600 dark:text-violet-400" />
          {t('categoryLines.title')}
        </span>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-md border border-border p-0.5 text-xs" role="group">
            {(['exact', 'percent'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => switchMode(mode)}
                aria-pressed={shareType === mode}
                className={cn(
                  'px-2 py-0.5 rounded-sm transition-colors cursor-pointer',
                  shareType === mode
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {mode === 'exact' ? t('categoryLines.byAmount') : t('categoryLines.byPercent')}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="space-y-2">
        {rows.map((row) => {
          // A category holds one part of a split; the ones other parts
          // already use are left out of this part's picker.
          const taken = new Set(
            rows.filter((r) => r.key !== row.key && r.categoryId).map((r) => r.categoryId),
          )
          // A part keeps its own category listed even when a saved split
          // repeats it, so the picker shows it rather than "hidden".
          taken.delete(row.categoryId)
          return (
          <div key={row.key} className="flex items-center gap-2">
            <div className="flex-1 min-w-0">
              <CategorySelect
                value={row.categoryId}
                onChange={(value) => update(row.key, { categoryId: value })}
                categories={usable.filter((c) => !taken.has(c.id))}
                groups={usableGroups.map((group) => ({
                  ...group,
                  categories: group.categories.filter((c) => !taken.has(c.id)),
                }))}
                currentCategory={row.category}
                placeholder={t('categoryLines.pickCategory')}
                className="bg-card"
              />
            </div>
            {shareType === 'exact' ? (
              <Input
                type="text"
                inputMode="decimal"
                aria-label={t('categoryLines.partAmount')}
                className="w-28 bg-card text-right tabular-nums"
                value={hideAmounts ? '•••••' : row.amount}
                readOnly={hideAmounts}
                onChange={(e) => update(row.key, { amount: e.target.value })}
              />
            ) : (
              <div className="flex items-center gap-1">
                <Input
                  type="text"
                  inputMode="decimal"
                  aria-label={t('categoryLines.partPercent')}
                  className="w-20 bg-card text-right tabular-nums"
                  value={row.percent}
                  onChange={(e) => update(row.key, { percent: e.target.value })}
                />
                <span className="text-xs text-muted-foreground">%</span>
              </div>
            )}
            <button
              type="button"
              onClick={() => remove(row.key)}
              disabled={rows.length <= 2}
              aria-label={t('categoryLines.removePart')}
              className="h-8 w-8 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 disabled:opacity-30 disabled:cursor-not-allowed shrink-0 cursor-pointer"
            >
              <X size={14} />
            </button>
          </div>
          )
        })}
      </div>

      {check.reason === 'duplicateCategory' && (
        <p className="text-xs text-red-600 dark:text-red-400">{t('categoryLines.errors.duplicateCategory')}</p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={add} className="h-7 px-2 text-xs">
          <Plus size={12} className="mr-1" />
          {t('categoryLines.addPart')}
        </Button>
        <div className="flex items-center gap-2 text-xs">
          {!hideAmounts && (
            check.remaining === 0 ? (
              check.valid && (
                <span className="text-emerald-600 dark:text-emerald-400">{t('categoryLines.allAllocated')}</span>
              )
            ) : check.remaining > 0 ? (
              <>
                <span className="text-amber-600 dark:text-amber-400">
                  {t('categoryLines.remaining', { value: remainingLabel })}
                </span>
                <button
                  type="button"
                  onClick={fillRemaining}
                  className="text-primary hover:underline font-medium cursor-pointer"
                >
                  {t('categoryLines.fillRemaining')}
                </button>
              </>
            ) : (
              <span className="text-red-600 dark:text-red-400">
                {t('categoryLines.over', { value: remainingLabel })}
              </span>
            )
          )}
        </div>
      </div>

      {check.reason === 'missingCategory' && (
        <p className="text-xs text-muted-foreground">{t('categoryLines.needsCategory')}</p>
      )}
      {check.remaining === 0 && (check.reason === 'zeroPart' || check.reason === 'invalidValue') && (
        <p className="text-xs text-muted-foreground">{t('categoryLines.needsValue')}</p>
      )}

      <div className="flex items-center justify-between gap-2 border-t border-border pt-2">
        <p className="text-xs text-muted-foreground">{t('categoryLines.hint')}</p>
        <Button type="button" variant="outline" size="sm" onClick={onUndo} className="h-7 px-2 text-xs shrink-0">
          {t('categoryLines.undo')}
        </Button>
      </div>
    </div>
  )
}
