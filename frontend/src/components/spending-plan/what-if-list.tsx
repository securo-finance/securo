import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, X } from 'lucide-react'
import { parseAmountInput } from '@/lib/format'
import { addDays } from '@/lib/spending-plan'
import { cn } from '@/lib/utils'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { Button } from '@/components/ui/button'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import type { PlanFormat } from './plan-format'
import type { WhatIfItem } from './use-what-if-items'

const EMPTY_FORM = { label: '', amount: '', firstDate: '', installments: '1' }

/** Hypothetical purchases the plan counts while they are on. */
export function WhatIfList({
  items,
  onChange,
  today,
  format,
}: {
  items: WhatIfItem[]
  onChange: (items: WhatIfItem[]) => void
  today: string
  format: PlanFormat
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const amount = parseAmountInput(form.amount, locale) ?? 0
  const installments = Math.round(Number(form.installments))
  const valid = form.label.trim() !== '' && amount > 0 && installments >= 1 && installments <= 120
  const update = (id: string, change: Partial<WhatIfItem>) =>
    onChange(items.map((item) => (item.id === id ? { ...item, ...change } : item)))

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        <span className="font-semibold text-foreground">{t('spendingPlan.whatIfTitle')}</span>{' '}
        {t('spendingPlan.whatIfHint')}
      </p>
      <ul className="flex flex-wrap gap-2">
        {items.map((item) => (
          <li
            key={item.id}
            className={cn(
              'inline-flex items-center gap-2 rounded-full border py-1 pl-2 pr-1 text-xs',
              item.enabled ? 'border-amber-400/70 bg-amber-50 dark:bg-amber-500/10' : 'border-border text-muted-foreground',
            )}
          >
            <Switch
              checked={item.enabled}
              onCheckedChange={(enabled) => update(item.id, { enabled })}
              aria-labelledby={`what-if-${item.id}`}
              className="scale-75"
            />
            <span id={`what-if-${item.id}`} className="tabular-nums">
              {item.label} {format.money(item.amount)}
              {item.installments > 1 && ` ${t('spendingPlan.whatIfInInstallments', { count: item.installments })}`}
              {' · '}
              {format.shortDate(item.first_date)}
            </span>
            <button
              type="button"
              className="rounded-full p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label={t('spendingPlan.whatIfRemove', { label: item.label })}
              onClick={() => onChange(items.filter((other) => other.id !== item.id))}
            >
              <X size={12} />
            </button>
          </li>
        ))}
        {!adding && (
          <li>
            <Button type="button" size="xs" variant="outline" className="rounded-full border-dashed" onClick={() => setAdding(true)}>
              <Plus /> {t('spendingPlan.whatIfAdd')}
            </Button>
          </li>
        )}
      </ul>
      {adding && (
        <form
          className="grid grid-cols-2 gap-2 rounded-lg border border-border p-3 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto_5rem]"
          onSubmit={(event) => {
            event.preventDefault()
            if (!valid) return
            onChange([...items, {
              id: crypto.randomUUID(),
              label: form.label.trim(),
              amount,
              first_date: form.firstDate || addDays(today, 30),
              installments,
              enabled: true,
            }])
            setForm(EMPTY_FORM)
            setAdding(false)
          }}
        >
          <div className="col-span-2 space-y-1 sm:col-span-1">
            <Label htmlFor="what-if-label" className="text-xs">{t('spendingPlan.whatIfLabel')}</Label>
            <Input
              id="what-if-label"
              value={form.label}
              placeholder={t('spendingPlan.whatIfLabelPlaceholder')}
              onChange={(event) => setForm((current) => ({ ...current, label: event.target.value }))}
              autoFocus
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="what-if-amount" className="text-xs">{t('spendingPlan.whatIfAmount')}</Label>
            <Input
              id="what-if-amount"
              inputMode="decimal"
              value={form.amount}
              placeholder="0,00"
              onChange={(event) => setForm((current) => ({ ...current, amount: event.target.value }))}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">{t('spendingPlan.whatIfFirstDate')}</Label>
            <DatePickerInput
              value={form.firstDate || addDays(today, 30)}
              onChange={(value) => setForm((current) => ({ ...current, firstDate: value }))}
              className="w-full justify-start"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="what-if-installments" className="text-xs">{t('spendingPlan.whatIfInstallments')}</Label>
            <Input
              id="what-if-installments"
              type="number"
              min={1}
              max={120}
              value={form.installments}
              onChange={(event) => setForm((current) => ({ ...current, installments: event.target.value }))}
            />
          </div>
          <div className="col-span-2 flex justify-end gap-2 sm:col-span-4">
            <Button type="button" size="xs" variant="ghost" onClick={() => { setAdding(false); setForm(EMPTY_FORM) }}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" size="xs" disabled={!valid}>{t('spendingPlan.whatIfAdd')}</Button>
          </div>
        </form>
      )}
    </div>
  )
}
