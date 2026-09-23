import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Plus, Repeat } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { PageHeader } from '@/components/page-header'
import { SectionCard, Segmented, TH } from '@/components/invoice-ui'
import { InvoiceLineEditor } from '@/components/invoice-line-editor'
import { EndConditionFields, ScheduleBadge } from '@/components/invoice-schedule-ui'
import { CurrencySelect } from '@/components/currency-select'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { invoiceSchedules as schedulesApi, invoices as invoicesApi, payees as payeesApi } from '@/lib/api'
import { invoiceErrorKey, linesTotal } from '@/lib/invoice-utils'
import { FREQUENCIES, endPayload, localToday, monthlyEquivalent } from '@/lib/invoice-schedule-utils'
import type {
  InvoiceLineInput,
  InvoiceSchedule,
  InvoiceScheduleEndType,
  InvoiceScheduleFrequency,
  InvoiceScheduleStatus,
} from '@/types'

/**
 * Recurring invoices: the agreements, what they are worth a month, and
 * which one needs a person.
 *
 * The summary is the financial reading of a subscription: what recurs,
 * what stopped recurring lately, and what is behind. All of it is
 * derived server-side from the invoices; nothing on this screen is a
 * number somebody typed.
 */
type Filter = 'all' | InvoiceScheduleStatus

export default function InvoiceSchedulesPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { canWrite } = useWorkspace()

  const [filter, setFilter] = useState<Filter>('all')
  const [createOpen, setCreateOpen] = useState(false)

  const { data: summary } = useQuery({
    queryKey: ['invoice-schedule-summary'],
    queryFn: schedulesApi.summary,
  })
  const { data: list, isLoading } = useQuery({
    queryKey: ['invoice-schedules', filter],
    queryFn: () => schedulesApi.list(filter === 'all' ? undefined : { status: filter }),
  })

  const money = (value: string | number | null | undefined, code: string) =>
    mask(formatCurrency(Number(value ?? 0), code, locale))
  const showDate = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(dateLocale)

  const counts = useMemo(
    () =>
      summary
        ? {
            all: summary.active_count + summary.paused_count + summary.ended_count,
            active: summary.active_count,
            paused: summary.paused_count,
            ended: summary.ended_count,
          }
        : undefined,
    [summary],
  )

  return (
    <div>
      <button
        onClick={() => navigate('/invoices')}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors mb-3"
        data-testid="schedules-back"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {t('invoices.backToList')}
      </button>

      <PageHeader
        section={t('invoices.title')}
        title={t('invoices.schedules.title')}
        action={
          canWrite ? (
            <Button size="sm" onClick={() => setCreateOpen(true)} data-testid="schedule-new-button">
              <Plus className="h-4 w-4 mr-1.5" />
              {t('invoices.schedules.new')}
            </Button>
          ) : undefined
        }
      />

      {/* One row per currency, in the invoices page's shape: the headline
          figure with what supports it beside it. An agreement in euros and
          one in reais never add up, so they never share a line. */}
      <div className="bg-card rounded-xl border border-border shadow-sm mb-5" data-testid="schedules-summary">
        {!summary ? (
          <div className="px-5 py-4">
            <Skeleton className="h-10 w-48" />
          </div>
        ) : summary.by_currency.length === 0 ? (
          <div className="px-5 py-4">
            <p className="text-xs font-medium text-muted-foreground mb-0.5">
              {t('invoices.schedules.summary.monthly')}
            </p>
            <p className="text-2xl font-semibold tracking-tight text-muted-foreground">
              {t('invoices.schedules.summary.none')}
            </p>
          </div>
        ) : (
          summary.by_currency.map((row) => (
            <div
              key={row.currency}
              className="grid grid-cols-2 sm:grid-cols-4 gap-y-3 px-5 py-4 border-b border-border last:border-0"
              data-testid={`schedules-summary-${row.currency}`}
            >
              <div className="col-span-2 sm:col-span-1">
                <p className="text-xs font-medium text-muted-foreground mb-0.5">
                  {t('invoices.schedules.summary.monthly')}
                </p>
                <p className="text-2xl font-semibold tracking-tight tabular-nums">
                  {money(row.monthly_recurring, row.currency)}
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {t('invoices.schedules.summary.activeCount', { count: row.active_count })}
                </p>
              </div>
              <Stat
                label={t('invoices.schedules.summary.yearly')}
                value={money(Number(row.monthly_recurring) * 12, row.currency)}
              />
              <Stat
                label={t('invoices.schedules.summary.lost')}
                value={money(row.monthly_lost, row.currency)}
                hint={t('invoices.schedules.summary.endedRecently', { count: row.ended_recently_count })}
                tone={Number(row.monthly_lost) > 0 ? 'text-rose-500' : undefined}
              />
              <Stat
                label={t('invoices.schedules.summary.pastDue')}
                value={String(row.past_due_count)}
                hint={t('invoices.schedules.summary.pastDueHint')}
                tone={row.past_due_count > 0 ? 'text-amber-600' : undefined}
              />
            </div>
          ))
        )}
      </div>

      <div className="mb-4">
        <Segmented<Filter>
          value={filter}
          onChange={setFilter}
          testIdPrefix="schedule-filter"
          options={(['all', 'active', 'paused', 'ended'] as const).map((value) => ({
            value,
            label: t(`invoices.schedules.filter.${value}`),
            count: counts?.[value],
          }))}
        />
      </div>

      <SectionCard>
        {isLoading ? (
          <div className="p-5 space-y-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : !list || list.length === 0 ? (
          <div className="px-5 py-14 text-center" data-testid="schedules-empty">
            <Repeat className="h-8 w-8 mx-auto text-muted-foreground/50" />
            <p className="mt-3 text-sm text-muted-foreground max-w-sm mx-auto">
              {filter === 'all'
                ? t('invoices.schedules.empty')
                : t('invoices.schedules.emptyFiltered')}
            </p>
            {filter === 'all' && canWrite && (
              <Button size="sm" className="mt-4" onClick={() => setCreateOpen(true)}>
                <Plus className="h-4 w-4 mr-1.5" />
                {t('invoices.schedules.new')}
              </Button>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border">
                  <th className={`${TH} pl-4 sm:pl-5 text-left`}>{t('invoices.schedules.column.name')}</th>
                  <th className={`${TH} text-left hidden md:table-cell`}>
                    {t('invoices.schedules.column.cadence')}
                  </th>
                  <th className={`${TH} text-right w-32 pr-6`}>{t('invoices.schedules.column.monthly')}</th>
                  <th className={`${TH} text-left w-36 hidden sm:table-cell`}>
                    {t('invoices.schedules.column.next')}
                  </th>
                  <th className={`${TH} text-right w-20 hidden lg:table-cell`}>
                    {t('invoices.schedules.column.invoices')}
                  </th>
                  <th className={`${TH} pr-4 sm:pr-5 text-right w-28`}>
                    {t('invoices.schedules.column.status')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((schedule) => (
                  <tr
                    key={schedule.id}
                    onClick={() => navigate(`/invoices/schedules/${schedule.id}`)}
                    data-testid="schedule-row"
                    className="border-b border-border last:border-0 hover:bg-muted transition-colors cursor-pointer"
                  >
                    <td className="py-3 pl-4 sm:pl-5">
                      <div className="text-sm font-medium text-foreground truncate">{schedule.name}</div>
                      <div className="text-xs text-muted-foreground truncate">
                        {schedule.payee?.name ?? t('invoices.noClient')}
                      </div>
                    </td>
                    <td className="py-3 hidden md:table-cell text-xs text-muted-foreground">
                      {t(`invoices.schedules.frequency.${schedule.frequency}`)}
                      {(schedule.current_term ?? schedule.next_term) && (
                        <span className="tabular-nums">
                          {' · '}
                          {money((schedule.current_term ?? schedule.next_term)!.total, schedule.currency)}
                        </span>
                      )}
                    </td>
                    <td className="py-3 pr-6 text-right text-sm font-bold tabular-nums">
                      {schedule.status === 'active'
                        ? money(schedule.monthly_amount, schedule.currency)
                        : <span className="text-muted-foreground font-medium">—</span>}
                    </td>
                    <td className="py-3 hidden sm:table-cell text-xs text-muted-foreground tabular-nums">
                      {schedule.status === 'active' && schedule.next_period_start
                        ? showDate(schedule.next_period_start)
                        : '—'}
                    </td>
                    <td className="py-3 text-right hidden lg:table-cell text-xs text-muted-foreground tabular-nums">
                      {schedule.invoice_count}
                    </td>
                    <td className="py-3 pr-4 sm:pr-5 text-right">
                      <ScheduleBadge schedule={schedule} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      <CreateScheduleDialog
        key={createOpen ? 'open' : 'closed'}
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(schedule) => navigate(`/invoices/schedules/${schedule.id}`)}
      />
    </div>
  )
}

function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: string
  hint?: string
  tone?: string
}) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground mb-0.5">{label}</p>
      <p className={cn('text-lg font-semibold tracking-tight tabular-nums', tone)}>{value}</p>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  )
}

function CreateScheduleDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (schedule: InvoiceSchedule) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const locale = useDisplayLocale()
  const { user } = useAuth()
  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
    enabled: open,
  })
  const { data: clients = [] } = useQuery({
    queryKey: ['payees', 'for-invoice'],
    queryFn: () => payeesApi.list({}),
    enabled: open,
  })

  const [name, setName] = useState('')
  const [payeeId, setPayeeId] = useState('')
  const [frequency, setFrequency] = useState<InvoiceScheduleFrequency>('monthly')
  const [startDate, setStartDate] = useState(() => localToday())
  const [endType, setEndType] = useState<InvoiceScheduleEndType>('never')
  const [endDate, setEndDate] = useState('')
  const [endCount, setEndCount] = useState('')
  const [paymentTerms, setPaymentTerms] = useState('')
  const [notes, setNotes] = useState('')
  const [currencyCode, setCurrencyCode] = useState(user?.preferences?.currency_display ?? 'USD')
  const [lines, setLines] = useState<InvoiceLineInput[]>([
    { description: '', quantity: '1', unit_price: '0' },
  ])

  const perPeriod = linesTotal(lines)
  const monthly = monthlyEquivalent(perPeriod, frequency)

  const mutation = useMutation({
    mutationFn: () =>
      schedulesApi.create({
        name,
        payee_id: payeeId || null,
        frequency,
        start_date: startDate,
        ...endPayload(endType, endDate, endCount),
        payment_terms_days: paymentTerms ? Number(paymentTerms) : null,
        currency: currencyCode,
        notes: notes || null,
        lines,
      }),
    onSuccess: (schedule) => {
      toast.success(t('invoices.schedules.created'))
      void queryClient.invalidateQueries({ queryKey: ['invoice-schedules'] })
      void queryClient.invalidateQueries({ queryKey: ['invoice-schedule-summary'] })
      onOpenChange(false)
      onCreated(schedule)
    },
    onError: (error) => {
      const key = invoiceErrorKey(error)
      toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
    },
  })

  const ready =
    name.trim().length > 0 &&
    perPeriod > 0 &&
    Boolean(startDate) &&
    (endType !== 'on_date' || Boolean(endDate)) &&
    (endType !== 'after_count' || Number(endCount) >= 1)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex flex-col max-h-[calc(100dvh-2rem)] sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t('invoices.schedules.new')}</DialogTitle>
          <DialogDescription>{t('invoices.schedules.newDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="schedule-name">{t('invoices.schedules.field.name')}</Label>
              <Input
                id="schedule-name"
                data-testid="schedule-name-input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('invoices.schedules.field.namePlaceholder')}
              />
            </div>
            <div className="space-y-1.5">
              <Label>{t('invoices.field.client')}</Label>
              <Select value={payeeId} onValueChange={setPayeeId}>
                <SelectTrigger data-testid="schedule-client-select">
                  <SelectValue placeholder={t('invoices.field.clientPlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  {clients.map((client) => (
                    <SelectItem key={client.id} value={client.id}>
                      {client.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="space-y-1.5">
              <Label>{t('invoices.schedules.field.frequency')}</Label>
              <Select value={frequency} onValueChange={(v) => setFrequency(v as InvoiceScheduleFrequency)}>
                <SelectTrigger data-testid="schedule-frequency-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FREQUENCIES.map((value) => (
                    <SelectItem key={value} value={value}>
                      {t(`invoices.schedules.frequency.${value}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="schedule-start">{t('invoices.schedules.field.startDate')}</Label>
              <Input
                id="schedule-start"
                data-testid="schedule-start-input"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="schedule-terms">{t('invoices.schedules.field.paymentTerms')}</Label>
              <Input
                id="schedule-terms"
                data-testid="schedule-terms-input"
                type="number"
                min={0}
                value={paymentTerms}
                onChange={(e) => setPaymentTerms(e.target.value)}
                placeholder={String(settings?.default_payment_terms_days ?? 30)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="schedule-currency">{t('invoices.schedules.field.currency')}</Label>
              <CurrencySelect id="schedule-currency" value={currencyCode} onChange={setCurrencyCode} />
            </div>
          </div>

          <EndConditionFields
            idPrefix="schedule"
            endType={endType}
            endDate={endDate}
            endCount={endCount}
            onChange={(next) => {
              setEndType(next.endType)
              setEndDate(next.endDate)
              setEndCount(next.endCount)
            }}
          />

          <InvoiceLineEditor
            lines={lines}
            onChange={setLines}
            currency={currencyCode}
            showTax={(settings?.tax_fields ?? 'hidden') !== 'hidden'}
            required
          />

          {/* What the agreement is worth a month, before it exists. The
              same normalisation the summary uses, so the number the
              person sees here is the number they will see there. */}
          <p className="text-xs text-muted-foreground" data-testid="schedule-monthly-preview">
            {t('invoices.schedules.monthlyPreview', {
              amount: formatCurrency(monthly, currencyCode, locale),
            })}
          </p>

          <div className="space-y-1.5">
            <Label htmlFor="schedule-notes">{t('invoices.field.notes')}</Label>
            <Input
              id="schedule-notes"
              data-testid="schedule-notes-input"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!ready || mutation.isPending}
            data-testid="schedule-create-submit"
          >
            {t('common.create')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
