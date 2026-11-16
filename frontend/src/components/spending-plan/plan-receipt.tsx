import { useState } from 'react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { planReceipt, type EventGroup } from '@/lib/spending-plan'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'
import type { SpendingPlanTimeline } from '@/types'
import { INCOME_TEXT, type PlanFormat } from './plan-format'

const GROUP_LABEL: Record<EventGroup, string> = {
  income: 'spendingPlan.groupIncome',
  fixed: 'spendingPlan.groupFixed',
  card: 'spendingPlan.groupCard',
  scenario: 'spendingPlan.whatIf',
  other: 'spendingPlan.otherMoves',
}

const NUM = 'whitespace-nowrap py-1.5 text-right tabular-nums'

/** Where the safe daily amount comes from: every move through the binding day, per day. */
export function PlanReceipt({ timeline, format }: { timeline: SpendingPlanTimeline; format: PlanFormat }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState<Set<EventGroup>>(() => new Set(['fixed']))
  const receipt = planReceipt(timeline)
  if (!receipt) return null
  const { days, through } = receipt
  const perDay = (value: number) => format.signed(value / days)
  const toggle = (key: EventGroup) =>
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  const horizonIsBinding = through === timeline.horizon_end

  return (
    <Card className="gap-0 p-5">
      <h2 className="text-base font-semibold">
        {t('spendingPlan.receiptTitle', { amount: format.money(timeline.safe_daily_allowance) })}
      </h2>
      <p className="mb-3 mt-0.5 text-sm text-muted-foreground">
        {t('spendingPlan.receiptHint', { count: days, date: format.shortDate(through) })}
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-muted-foreground">
            <th>
              <span className="sr-only">{t('spendingPlan.sheetWhat')}</span>
            </th>
            <th className="w-36 py-1 text-right font-normal">
              {t('spendingPlan.receiptThrough', { date: format.shortDate(through) })}
            </th>
            <th className="w-32 py-1 text-right font-normal">{t('spendingPlan.receiptPerDay')}</th>
          </tr>
        </thead>
        <tbody className="[&_td]:border-b [&_td]:border-dashed [&_td]:border-border">
          <ReceiptRow
            label={t('spendingPlan.receiptOpening')}
            amount={receipt.opening}
            perDay={perDay}
            format={format}
          />
          {receipt.groups.map(({ key, bucket }) => {
            const expandable = bucket.items.length > 0
            const isOpen = expandable && open.has(key)
            const label = (
              <>
                {t(GROUP_LABEL[key])}
                {key === 'scenario' && (
                  <span className="ml-1.5 rounded border border-amber-500 px-1 text-[10px] font-semibold uppercase tracking-wide text-amber-600 dark:text-amber-400">
                    {t('spendingPlan.simulationBadge')}
                  </span>
                )}
                {expandable && (
                  <span className="ml-1.5 font-normal text-muted-foreground">· {bucket.items.length}</span>
                )}
              </>
            )
            return [
              <ReceiptRow
                key={key}
                label={
                  expandable ? (
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      onClick={() => toggle(key)}
                      className="inline-flex items-center gap-1 text-left"
                    >
                      {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      {label}
                    </button>
                  ) : (
                    label
                  )
                }
                amount={bucket.total}
                perDay={perDay}
                format={format}
              />,
              ...(isOpen
                ? bucket.items.map((item, index) => (
                    <tr key={`${key}-${index}`} className="text-xs text-muted-foreground">
                      <td className="py-1 pl-6">
                        {item.label ?? t(key === 'scenario' ? 'spendingPlan.whatIf' : 'spendingPlan.otherMoves')}
                      </td>
                      <td className={NUM}>{format.signed(item.amount)}</td>
                      <td className={NUM}>{perDay(item.amount)}</td>
                    </tr>
                  ))
                : []),
            ]
          })}
          {receipt.spentToday > 0 && (
            <ReceiptRow
              label={t('spendingPlan.receiptSpentToday')}
              amount={receipt.spentToday}
              perDay={perDay}
              format={format}
            />
          )}
          {receipt.floor > 0 && (
            <ReceiptRow
              label={t('spendingPlan.receiptFloor')}
              amount={-receipt.floor}
              perDay={perDay}
              format={format}
            />
          )}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-foreground text-base font-bold">
            <td className="pt-2">{t('spendingPlan.receiptFree')}</td>
            <td className="pt-2 text-right tabular-nums">{format.money(receipt.free)}</td>
            <td data-testid="receipt-per-day" className="pt-2 text-right tabular-nums">
              {format.money(receipt.perDay)}
            </td>
          </tr>
        </tfoot>
      </table>
      <div className="mt-4 rounded-lg bg-primary/5 px-4 py-3 text-sm dark:bg-primary/10">
        {horizonIsBinding ? (
          <p>{t('spendingPlan.receiptWhyEnd')}</p>
        ) : (
          <p>
            <span className="font-semibold">
              {t('spendingPlan.receiptWhy', {
                binding: format.shortDate(through),
                end: format.shortDate(timeline.horizon_end),
              })}
            </span>{' '}
            {t('spendingPlan.receiptWhyBody', { binding: format.shortDate(through) })}
          </p>
        )}
        <p className="mt-1">
          {t('spendingPlan.receiptCost', {
            thousand: format.money(1000),
            date: format.shortDate(through),
            amount: format.money(1000 / days),
          })}
        </p>
      </div>
    </Card>
  )
}

function ReceiptRow({
  label,
  amount,
  perDay,
  format,
}: {
  label: ReactNode
  amount: number
  perDay: (value: number) => string
  format: PlanFormat
}) {
  return (
    <tr className="font-medium">
      <td className="py-1.5">{label}</td>
      <td className={cn(NUM, amount > 0 && INCOME_TEXT)}>{format.signed(amount)}</td>
      <td className={cn(NUM, amount > 0 && INCOME_TEXT)}>{perDay(amount)}</td>
    </tr>
  )
}
