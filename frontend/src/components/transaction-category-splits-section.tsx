import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { Trash2, Plus } from 'lucide-react'

import { formatCurrency, formatAmountInput, parseAmountInput } from '@/lib/format'
import type { CategoryAllocationsInput } from '@/types'
import type { Category, CategoryGroup } from '@/types'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { CategorySelect } from '@/components/category-select'

interface AllocationRow {
  category_id: string
  amount: string
  notes?: string | null
}

const BLANK_ROW: AllocationRow = { category_id: '', amount: '' }

function buildRows(value: CategoryAllocationsInput | null): AllocationRow[] {
  if (!value || value.allocations.length === 0) return [{ ...BLANK_ROW }, { ...BLANK_ROW }]
  return value.allocations.map((a) => ({ category_id: a.category_id, amount: a.amount, notes: a.notes }))
}

export function TransactionCategorySplitsSection({
  amount,
  currency,
  value,
  onChange,
  onValidityChange,
  categories,
  categoryGroups,
}: {
  amount: number
  currency: string
  value: CategoryAllocationsInput | null
  onChange: (v: CategoryAllocationsInput | null) => void
  onValidityChange: (valid: boolean) => void
  categories: Category[]
  categoryGroups: CategoryGroup[]
}) {
  const { t } = useTranslation()
  const displayLocale = useDisplayLocale()
  const rows = buildRows(value)

  const total = amount

  const rowSum = rows.reduce((acc, r) => {
    const n = parseAmountInput(r.amount, displayLocale)
    return acc + (n ?? 0)
  }, 0)

  const categoryIds = rows.map((r) => r.category_id).filter((id) => id !== '')
  const isValid =
    rows.length >= 2 &&
    rows.every((r) => r.category_id !== '') &&
    rows.every((r) => (parseAmountInput(r.amount, displayLocale) ?? 0) > 0) &&
    new Set(categoryIds).size === categoryIds.length &&
    Math.abs(rowSum - total) < 0.01

  useEffect(() => {
    onValidityChange(isValid)
  }, [isValid, onValidityChange])

  function update(index: number, patch: Partial<AllocationRow>) {
    const next = rows.map((r, i) => (i === index ? { ...r, ...patch } : r))
    emit(next)
  }

  function addRow() {
    emit([...rows, { ...BLANK_ROW }])
  }

  function removeRow(index: number) {
    if (rows.length <= 2) return
    emit(rows.filter((_, i) => i !== index))
  }

  function emit(next: AllocationRow[]) {
    onChange({
      allocations: next.map((r) => ({
        category_id: r.category_id,
        amount: r.amount,
        notes: r.notes,
      })),
    })
  }

  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">{t('splitGroups.categorySplits.title')}</div>

      <div className="space-y-2">
        {rows.map((row, i) => (
          <div key={i} className="flex items-center gap-2">
            <div className="flex-1 min-w-0">
              <CategorySelect
                value={row.category_id || ''}
                onChange={(id) => update(i, { category_id: id ?? '' })}
                categories={categories}
                groups={categoryGroups}
                placeholder={t('splitGroups.categorySplits.category')}
                allowNone
              />
            </div>
            <div className="w-28">
              <Input
                type="text"
                inputMode="decimal"
                placeholder="0.00"
                value={row.amount}
                onChange={(e) => update(i, { amount: e.target.value })}
                className="text-right"
              />
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0 text-muted-foreground"
              disabled={rows.length <= 2}
              onClick={() => removeRow(i)}
              aria-label={t('splitGroups.categorySplits.remove')}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
      </div>

      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="w-full border border-dashed text-muted-foreground"
        onClick={addRow}
      >
        <Plus className="mr-1 h-4 w-4" />
        {t('splitGroups.categorySplits.addRow')}
      </Button>

      <div className={`text-xs text-right ${isValid ? 'text-muted-foreground' : 'text-destructive'}`}>
        {t('splitGroups.categorySplits.sum', {
          sum: formatCurrency(rowSum, currency, displayLocale),
          total: formatCurrency(total, currency, displayLocale),
          currency,
        })}
      </div>
    </div>
  )
}
