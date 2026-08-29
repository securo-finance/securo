import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { SplitSquareHorizontal, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { formatCurrency } from '@/lib/format'
import type { CategorySplitInput, Category, CategoryGroup } from '@/types'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { CategorySelect } from '@/components/category-select'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

export function CategorySplitsSection({
  amount,
  currency,
  value,
  onChange,
  onValidityChange,
  categories,
  categoryGroups,
  accounts,
}: {
  amount: number
  currency: string
  value: CategorySplitInput[] | null
  onChange: (next: CategorySplitInput[] | null) => void
  onValidityChange?: (valid: boolean) => void
  categories: Category[]
  categoryGroups: CategoryGroup[]
  accounts: { id: string; name: string; display_name?: string | null; type?: string }[]
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const [enabled, setEnabled] = useState(value !== null && value.length > 0)
  const [rows, setRows] = useState<Array<{ id: string; amount: string; category_id: string | null; transfer_account_id: string | null; notes: string }>>(() => {
    if (value && value.length > 0) {
      return value.map((v, i) => ({
        id: `init-${i}`,
        amount: v.amount.toString(),
        category_id: v.category_id,
        transfer_account_id: v.transfer_account_id ?? null,
        notes: v.notes ?? '',
      }))
    }
    return [
      { id: crypto.randomUUID(), amount: '', category_id: null, transfer_account_id: null, notes: '' },
      { id: crypto.randomUUID(), amount: '', category_id: null, transfer_account_id: null, notes: '' },
    ]
  })

  // Push state up whenever it changes
  useEffect(() => {
    if (!enabled) {
      onChange(null)
      return
    }
    const splits: CategorySplitInput[] = rows.map((r) => ({
      amount: r.amount ? parseFloat(r.amount) : 0,
      category_id: r.category_id,
      transfer_account_id: r.transfer_account_id,
      notes: r.notes.trim() || null,
    }))
    onChange(splits)
  }, [enabled, rows, onChange])

  const total = useMemo(() => {
    if (!enabled) return null
    return rows.reduce((sum, r) => sum + (parseFloat(r.amount) || 0), 0)
  }, [enabled, rows])

  const isValid = useMemo(() => {
    if (!enabled) return true
    if (rows.length < 2) return false
    const sum = rows.reduce((s, r) => s + (parseFloat(r.amount) || 0), 0)
    if (Math.abs(sum - Math.abs(amount)) > 0.005) return false
    // Also require either a category or a transfer account for each split
    if (rows.some(r => !r.category_id && !r.transfer_account_id)) return false
    return true
  }, [enabled, rows, amount])

  useEffect(() => {
    onValidityChange?.(isValid)
  }, [isValid, onValidityChange])

  const updateRow = (id: string, patch: Partial<typeof rows[0]>) => {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  }

  const addRow = () => {
    setRows((prev) => [...prev, { id: crypto.randomUUID(), amount: '', category_id: null, transfer_account_id: null, notes: '' }])
  }

  const removeRow = (id: string) => {
    setRows((prev) => prev.filter((r) => r.id !== id))
  }

  return (
    <div className="space-y-3 pt-2 border-t border-border">
      <label className="text-sm font-medium inline-flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="h-4 w-4 rounded border-border accent-primary"
        />
        <SplitSquareHorizontal size={14} />
        {t('transactions.splitAcrossCategories', 'Split across categories')}
      </label>

      {enabled && (
        <div className="space-y-3 pl-6">
          <div className="space-y-2">
            {rows.map((row, index) => (
              <div key={row.id} className="flex items-start gap-2">
                <div className="flex-1 min-w-0 space-y-2">
                  <div className="flex items-center gap-2">
                    <Input
                      type="number"
                      step="0.01"
                      placeholder={t('transactions.amount')}
                      className="w-24 text-sm"
                      value={row.amount}
                      onChange={(e) => updateRow(row.id, { amount: e.target.value })}
                    />
                    <div className="flex-1 min-w-0 flex items-center gap-2">
                      <select
                        className="w-[120px] shrink-0 border border-border rounded-md px-2 py-1.5 text-sm bg-card"
                        value={row.transfer_account_id ? 'transfer' : 'category'}
                        onChange={(e) => {
                          if (e.target.value === 'transfer') {
                            updateRow(row.id, { transfer_account_id: accounts[0]?.id || '', category_id: null })
                          } else {
                            updateRow(row.id, { transfer_account_id: null, category_id: '' })
                          }
                        }}
                      >
                        <option value="category">{t('transactions.category')}</option>
                        <option value="transfer">{t('transactions.transfer')}</option>
                      </select>
                      
                      {row.transfer_account_id ? (
                        <Select
                          value={row.transfer_account_id}
                          onValueChange={(val) => updateRow(row.id, { transfer_account_id: val })}
                        >
                          <SelectTrigger className="flex-1 min-w-0">
                            <SelectValue placeholder={t('transactions.selectAccount')} />
                          </SelectTrigger>
                          <SelectContent>
                            {accounts.map(acc => (
                              <SelectItem key={acc.id} value={acc.id}>
                                {acc.display_name || acc.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <CategorySelect
                          value={row.category_id || ''}
                          onChange={(val) => updateRow(row.id, { category_id: val })}
                          categories={categories}
                          categoryGroups={categoryGroups}
                        />
                      )}
                    </div>
                  </div>
                  <Input
                    type="text"
                    placeholder={t('transactions.notes')}
                    className="w-full text-sm h-8"
                    value={row.notes}
                    onChange={(e) => updateRow(row.id, { notes: e.target.value })}
                  />
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="shrink-0 text-muted-foreground hover:text-destructive"
                  onClick={() => removeRow(row.id)}
                  disabled={rows.length <= 2}
                >
                  <Trash2 size={16} />
                </Button>
              </div>
            ))}
          </div>

          <div className="flex items-center justify-between mt-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={addRow}
              className="text-xs"
            >
              <Plus size={14} className="mr-1" />
              {t('transactions.addSplit', 'Add split')}
            </Button>
            
            {total !== null && (
              <div className="text-xs text-muted-foreground">
                <span
                  className={
                    Math.abs(total - Math.abs(amount)) < 0.005
                      ? 'text-emerald-600'
                      : 'text-amber-600'
                  }
                >
                  {t('splitGroups.amountSum', {
                    total: total.toFixed(2),
                    target: Math.abs(amount).toFixed(2),
                    currency,
                  })}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
