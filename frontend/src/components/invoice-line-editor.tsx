import { useTranslation } from 'react-i18next'
import { Plus, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { linesTotal } from '@/lib/invoice-utils'
import type { InvoiceLineInput } from '@/types'

/**
 * Line items on a draft.
 *
 * Optional by design, and the empty state says so: under the tracking
 * preset the fiscal document was issued elsewhere and an invoice with no
 * lines is the normal case, not an unfinished one.
 *
 * The layout is a table with real headers rather than a row of bare
 * boxes. A field holding `1` next to a field holding `0` tells the
 * person nothing about which is a quantity and which is a price, and a
 * placeholder disappears the moment they type. Each row also shows what
 * it comes to, because the arithmetic between a rate and a total is the
 * thing most worth checking before sending an invoice out.
 *
 * The running totals here are a convenience while typing; the server
 * recomputes on save from the same quantities and prices, and its answer
 * is the one that gets stored.
 */
const BLANK: InvoiceLineInput = { description: '', quantity: '1', unit_price: '0' }

/** Table header cell, matching the module's other tables. */
const TH = 'text-[11px] font-medium text-muted-foreground pb-1.5'

function lineAmount(line: InvoiceLineInput): number {
  const quantity = Number(line.quantity)
  const price = Number(line.unit_price)
  if (!Number.isFinite(quantity) || !Number.isFinite(price)) return 0
  return quantity * price
}

export function InvoiceLineEditor({
  lines,
  onChange,
  currency,
  showTax,
  required = false,
}: {
  lines: InvoiceLineInput[]
  onChange: (lines: InvoiceLineInput[]) => void
  currency: string
  showTax: boolean
  /** True when the workspace's preset makes the document mandatory. Shows
   *  one empty row so the requirement is visible before the submit, not
   *  after it. */
  required?: boolean
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()

  // A render decision, not state: seeding the parent's array from an
  // effect would fight the parent for ownership of it.
  const rows = lines.length === 0 && required ? [BLANK] : lines

  const update = (index: number, patch: Partial<InvoiceLineInput>) => {
    onChange(rows.map((line, i) => (i === index ? { ...line, ...patch } : line)))
  }

  const add = () => onChange([...rows, { ...BLANK }])
  const money = (value: number) => formatCurrency(value, currency, locale)
  const total = linesTotal(rows)

  return (
    <div className="space-y-2" data-testid="invoice-line-editor">
      <div className="flex items-center justify-between">
        <Label>{t('invoices.field.lines')}</Label>
        {rows.length > 0 && (
          <Button size="sm" variant="ghost" onClick={add} data-testid="invoice-add-line">
            <Plus className="h-3.5 w-3.5 mr-1" />
            {t('invoices.field.addLine')}
          </Button>
        )}
      </div>

      {rows.length === 0 ? (
        <button
          type="button"
          onClick={add}
          data-testid="invoice-add-line"
          className="w-full rounded-lg border border-dashed border-border px-4 py-5 text-center hover:border-primary/40 hover:bg-primary/[0.02] transition-colors"
        >
          <span className="flex items-center justify-center gap-1.5 text-sm font-medium text-foreground">
            <Plus className="h-4 w-4" />
            {t('invoices.field.addLine')}
          </span>
          <span className="mt-1 block text-xs text-muted-foreground">
            {t('invoices.field.linesOptional')}
          </span>
        </button>
      ) : (
        <div className="rounded-lg border border-border overflow-hidden">
          {/* Headers on the wide layout only: on a narrow screen the rows
              stack and each field carries its own label instead. */}
          <div className="hidden sm:grid grid-cols-[1fr_4.5rem_5rem_7rem_6rem_2rem] gap-2 px-3 pt-2.5 bg-muted/40">
            <span className={TH}>{t('invoices.field.lineDescription')}</span>
            <span className={`${TH} text-right`}>{t('invoices.column.quantity')}</span>
            <span className={TH}>{t('invoices.field.unit')}</span>
            <span className={`${TH} text-right`}>{t('invoices.field.unitPrice')}</span>
            <span className={`${TH} text-right`}>{t('invoices.column.amount')}</span>
            <span className={TH} />
          </div>

          <div className="divide-y divide-border">
            {rows.map((line, index) => (
              <div
                key={index}
                data-testid="invoice-line-row"
                className="grid grid-cols-2 sm:grid-cols-[1fr_4.5rem_5rem_7rem_6rem_2rem] gap-2 px-3 py-2.5 items-center"
              >
                <div className="col-span-2 sm:col-span-1">
                  <Input
                    className="h-9"
                    placeholder={t('invoices.field.lineDescription')}
                    value={line.description}
                    onChange={(e) => update(index, { description: e.target.value })}
                    data-testid={`invoice-line-description-${index}`}
                    aria-label={t('invoices.field.lineDescription')}
                  />
                </div>

                <Field label={t('invoices.column.quantity')}>
                  <Input
                    className="h-9 text-right"
                    inputMode="decimal"
                    value={line.quantity}
                    onChange={(e) => update(index, { quantity: e.target.value })}
                    data-testid={`invoice-line-quantity-${index}`}
                    aria-label={t('invoices.column.quantity')}
                  />
                </Field>

                {/* Beside the number it qualifies, and optional: a line
                    counting nothing in particular reads fine without. */}
                <Field label={t('invoices.field.unit')}>
                  <Input
                    className="h-9"
                    placeholder={t('invoices.field.unitPlaceholder')}
                    value={line.unit ?? ''}
                    onChange={(e) => update(index, { unit: e.target.value || null })}
                    data-testid={`invoice-line-unit-${index}`}
                    aria-label={t('invoices.field.unit')}
                    maxLength={20}
                  />
                </Field>

                <Field label={t('invoices.field.unitPrice')}>
                  <Input
                    className="h-9 text-right"
                    inputMode="decimal"
                    value={line.unit_price}
                    onChange={(e) => update(index, { unit_price: e.target.value })}
                    data-testid={`invoice-line-price-${index}`}
                    aria-label={t('invoices.field.unitPrice')}
                  />
                </Field>

                {/* What this row comes to. Not an input: it is the product
                    of the two fields beside it, and a place to type it
                    would be a place for it to disagree with them. */}
                <div className="text-right">
                  <span className="sm:hidden text-[11px] text-muted-foreground block">
                    {t('invoices.column.amount')}
                  </span>
                  <span
                    className="text-sm font-medium tabular-nums"
                    data-testid={`invoice-line-amount-${index}`}
                  >
                    {money(lineAmount(line))}
                  </span>
                </div>

                <div className="flex justify-end">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive"
                    onClick={() => onChange(rows.filter((_, i) => i !== index))}
                    data-testid={`invoice-remove-line-${index}`}
                    aria-label={t('common.delete')}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>

                {/* Only when the workspace shows tax at all — a rate field
                    on a tracking-preset invoice is a question nobody
                    asked. On its own row so the columns above stay put. */}
                {showTax && (
                  <div className="col-span-2 sm:col-span-6 flex items-center gap-2 pt-1">
                    <span className="text-[11px] text-muted-foreground">
                      {t('invoices.field.taxRate')}
                    </span>
                    <Input
                      className="h-8 w-20 text-right"
                      inputMode="decimal"
                      placeholder="%"
                      value={line.tax_rate ?? ''}
                      onChange={(e) => update(index, { tax_rate: e.target.value || null })}
                      data-testid={`invoice-line-tax-${index}`}
                      aria-label={t('invoices.field.taxRate')}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="flex items-center justify-between px-3 py-2.5 bg-muted/40 border-t border-border">
            <span className="text-xs text-muted-foreground">
              {t('invoices.field.lineCount', { count: rows.length })}
            </span>
            <span className="text-sm">
              <span className="text-muted-foreground mr-2">{t('invoices.column.total')}</span>
              <span className="font-semibold tabular-nums" data-testid="invoice-lines-total">
                {money(total)}
              </span>
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

/** A field whose label shows only on the stacked layout, where the table
 *  header is not there to explain it. */
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="sm:hidden text-[11px] text-muted-foreground block mb-0.5">{label}</span>
      {children}
    </div>
  )
}
