import { useTranslation } from 'react-i18next'
import { Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { cn } from '@/lib/utils'
import { addMonths, installmentsTotal, splitEvenly } from '@/lib/installment-utils'
import type { InstallmentInput } from '@/types'

/**
 * The dates the money is expected on, when there is more than one.
 *
 * Off by default and one switch away: the ordinary invoice has one due
 * date and never sees this. On, it is a small table the person fills or
 * seeds with "split in N", and a running check against the total,
 * because the server refuses a schedule that does not add up and the
 * mismatch is better shown here than as an error after the submit.
 */
const TH = 'text-[11px] font-medium text-muted-foreground pb-1.5'
const SPLITS = [2, 3, 4, 6, 12]

export function InvoiceInstallmentsEditor({
  value,
  onChange,
  total,
  currency,
  firstDueDate,
  minDate,
}: {
  /** Null means "one due date, no schedule". */
  value: InstallmentInput[] | null
  onChange: (value: InstallmentInput[] | null) => void
  total: number
  currency: string
  /** Where "split in N" starts counting from: the invoice's due date. */
  firstDueDate: string
  /** Nothing may be due before the issue date. */
  minDate?: string
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const enabled = value !== null
  const rows = value ?? []
  const scheduled = installmentsTotal(rows)
  const mismatch = enabled && Math.abs(scheduled - total) >= 0.005
  const money = (n: number) => formatCurrency(n, currency, locale)

  const update = (index: number, patch: Partial<InstallmentInput>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  return (
    <div className="space-y-2" data-testid="invoice-installments-editor">
      <div className="flex items-center justify-between">
        <Label htmlFor="installments-toggle">{t('invoices.installments.title')}</Label>
        <Switch
          id="installments-toggle"
          checked={enabled}
          onCheckedChange={(on) =>
            onChange(on ? splitEvenly(total, 2, firstDueDate) : null)
          }
        />
      </div>

      {enabled && (
        <div className="rounded-lg border border-border overflow-hidden">
          <div className="flex flex-wrap items-center gap-2 px-3 py-2 bg-muted/40 border-b border-border">
            <span className="text-xs text-muted-foreground">{t('invoices.installments.splitIn')}</span>
            <Select onValueChange={(n) => onChange(splitEvenly(total, Number(n), firstDueDate))}>
              <SelectTrigger className="h-8 w-24" data-testid="installments-split">
                <SelectValue placeholder="N" />
              </SelectTrigger>
              <SelectContent>
                {SPLITS.map((n) => (
                  <SelectItem key={n} value={String(n)}>{`${n}x`}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-[11px] text-muted-foreground">{t('invoices.installments.splitHint')}</span>
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto"
              onClick={() =>
                onChange([
                  ...rows,
                  { due_date: addMonths(rows[rows.length - 1]?.due_date ?? firstDueDate, 1), amount: '0', label: null },
                ])
              }
              data-testid="installments-add"
            >
              <Plus className="h-3.5 w-3.5 mr-1" />
              {t('invoices.installments.add')}
            </Button>
          </div>

          <div className="hidden sm:grid grid-cols-[1fr_8rem_8rem_2rem] gap-2 px-3 pt-2">
            <span className={TH}>{t('invoices.installments.label')}</span>
            <span className={TH}>{t('invoices.column.due')}</span>
            <span className={`${TH} text-right`}>{t('invoices.column.amount')}</span>
            <span className={TH} />
          </div>
          <div className="divide-y divide-border">
            {rows.map((row, index) => (
              <div
                key={index}
                data-testid="installment-row"
                className="grid grid-cols-2 sm:grid-cols-[1fr_8rem_8rem_2rem] gap-2 px-3 py-2 items-center"
              >
                <Input
                  className="h-9 col-span-2 sm:col-span-1"
                  placeholder={t('invoices.installments.labelPlaceholder', { n: index + 1, total: rows.length })}
                  value={row.label ?? ''}
                  onChange={(e) => update(index, { label: e.target.value || null })}
                  data-testid={`installment-label-${index}`}
                  maxLength={60}
                />
                <Input
                  className="h-9"
                  type="date"
                  min={minDate}
                  value={row.due_date}
                  onChange={(e) => update(index, { due_date: e.target.value })}
                  data-testid={`installment-due-${index}`}
                  aria-label={t('invoices.column.due')}
                />
                <Input
                  className="h-9 text-right"
                  inputMode="decimal"
                  value={row.amount}
                  onChange={(e) => update(index, { amount: e.target.value })}
                  data-testid={`installment-amount-${index}`}
                  aria-label={t('invoices.column.amount')}
                />
                <div className="flex justify-end">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                    disabled={rows.length <= 2}
                    onClick={() => onChange(rows.filter((_, i) => i !== index))}
                    data-testid={`installment-remove-${index}`}
                    aria-label={t('common.delete')}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            ))}
          </div>

          <div
            className={cn(
              'flex items-center justify-between px-3 py-2 text-xs border-t border-border',
              mismatch ? 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-400' : 'bg-muted/40 text-muted-foreground',
            )}
            data-testid="installments-check"
          >
            <span>
              {mismatch
                ? t('invoices.installments.mismatch', { scheduled: money(scheduled), total: money(total) })
                : t('invoices.installments.addsUp', { total: money(total) })}
            </span>
            <span className="tabular-nums font-medium">{money(scheduled)}</span>
          </div>
        </div>
      )}
    </div>
  )
}
