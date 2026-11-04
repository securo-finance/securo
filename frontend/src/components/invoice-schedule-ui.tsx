import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Repeat } from 'lucide-react'

import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { periodLabel, scheduleLabel, scheduleTone } from '@/lib/invoice-schedule-utils'
import { useDateLocale } from '@/hooks/use-display-locale'
import type { Invoice, InvoiceSchedule, InvoiceScheduleEndType } from '@/types'

/** Status pill for an agreement, in the invoice badge's shape. */
export function ScheduleBadge({
  schedule,
}: {
  schedule: Pick<InvoiceSchedule, 'status' | 'pause_reason' | 'past_due_count'>
}) {
  const { t } = useTranslation()
  const label = scheduleLabel(schedule)
  return (
    <span
      data-testid={`schedule-state-${label}`}
      className={cn(
        'text-[11px] font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap',
        scheduleTone(schedule),
      )}
    >
      {t(`invoices.schedules.state.${label}`)}
    </span>
  )
}

/**
 * The chip an invoice wears when it answers for a period of an
 * agreement: the agreement's name and the period, linking to it. Small
 * on purpose: the invoice is the subject, this is its provenance.
 */
export function SchedulePeriodChip({ invoice }: { invoice: Invoice }) {
  const dateLocale = useDateLocale()
  if (!invoice.schedule || !invoice.period_start || !invoice.period_end) return null
  return (
    <Link
      to={`/invoices/schedules/${invoice.schedule.id}`}
      onClick={(e) => e.stopPropagation()}
      data-testid="invoice-schedule-chip"
      className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground hover:text-foreground transition-colors"
    >
      <Repeat className="h-3 w-3" />
      <span className="truncate max-w-[160px]">{invoice.schedule.name}</span>
      <span className="text-muted-foreground/70 tabular-nums">
        {periodLabel(invoice.period_start, invoice.period_end, dateLocale)}
      </span>
    </Link>
  )
}

/**
 * The end condition, as one control. Three shapes (never, a date, a
 * count) share a select, and the field for the chosen one appears
 * beside it. Used by the create and edit dialogs and the "make
 * recurring" one on the invoice page, so the three agree.
 */
export function EndConditionFields({
  endType,
  endDate,
  endCount,
  onChange,
  idPrefix,
}: {
  endType: InvoiceScheduleEndType
  endDate: string
  endCount: string
  onChange: (next: { endType: InvoiceScheduleEndType; endDate: string; endCount: string }) => void
  idPrefix: string
}) {
  const { t } = useTranslation()
  return (
    <div className="grid grid-cols-2 gap-3">
      <div className="space-y-1.5">
        <Label>{t('invoices.schedules.field.ends')}</Label>
        <Select
          value={endType}
          onValueChange={(value) => onChange({ endType: value as InvoiceScheduleEndType, endDate, endCount })}
        >
          <SelectTrigger data-testid={`${idPrefix}-end-type`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(['never', 'on_date', 'after_count'] as const).map((value) => (
              <SelectItem key={value} value={value}>
                {t(`invoices.schedules.endType.${value}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {endType === 'on_date' && (
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-end-date`}>{t('invoices.schedules.field.endDate')}</Label>
          <Input
            id={`${idPrefix}-end-date`}
            data-testid={`${idPrefix}-end-date`}
            type="date"
            value={endDate}
            onChange={(e) => onChange({ endType, endDate: e.target.value, endCount })}
          />
        </div>
      )}
      {endType === 'after_count' && (
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-end-count`}>{t('invoices.schedules.field.endCount')}</Label>
          <Input
            id={`${idPrefix}-end-count`}
            data-testid={`${idPrefix}-end-count`}
            type="number"
            min={1}
            value={endCount}
            onChange={(e) => onChange({ endType, endDate, endCount: e.target.value })}
          />
        </div>
      )}
    </div>
  )
}
