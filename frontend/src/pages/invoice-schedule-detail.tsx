import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Ban, Link2, MoreHorizontal, Pause, Pencil, Play, Plus, Receipt,
  Repeat, Trash2, Zap,
} from 'lucide-react'
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { PageHeader } from '@/components/page-header'
import { IconAction, SectionCard, SectionHeader, StateBadge, TH } from '@/components/invoice-ui'
import { EndConditionFields, ScheduleBadge } from '@/components/invoice-schedule-ui'
import { InvoiceLineEditor } from '@/components/invoice-line-editor'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useWorkspace } from '@/contexts/workspace-context'
import { invoiceSchedules as schedulesApi, invoices as invoicesApi, payees as payeesApi } from '@/lib/api'
import { displayNumber, invoiceErrorKey, linesTotal } from '@/lib/invoice-utils'
import {
  FREQUENCIES,
  endPayload,
  isUpcomingTerm,
  localToday,
  monthlyEquivalent,
  periodLabel,
  scheduleActions,
} from '@/lib/invoice-schedule-utils'
import type {
  InvoiceLineInput,
  InvoiceSchedule,
  InvoiceScheduleEndReason,
  InvoiceScheduleEndType,
  InvoiceScheduleFrequency,
  InvoiceScheduleTerm,
} from '@/types'

/**
 * One agreement: what it is worth, what it has produced, and the price
 * over time.
 *
 * Two sections carry the design. **Terms** is the price as a history:
 * a raise recorded today for January sits there as an upcoming row and
 * changes nothing until then. **Invoices** is the money: every period
 * that was billed, born from the job or linked by hand, each an
 * ordinary invoice that knows which period it answers for.
 */
export default function InvoiceScheduleDetailPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { canWrite } = useWorkspace()

  const [editOpen, setEditOpen] = useState(false)
  const [endOpen, setEndOpen] = useState(false)
  const [linkOpen, setLinkOpen] = useState(false)
  const [termTarget, setTermTarget] = useState<InvoiceScheduleTerm | 'new' | null>(null)

  const { data: schedule, isLoading } = useQuery({
    queryKey: ['invoice-schedule', id],
    queryFn: () => schedulesApi.get(id),
    enabled: Boolean(id),
  })
  const { data: invoices = [] } = useQuery({
    queryKey: ['invoice-schedule-invoices', id],
    queryFn: () => schedulesApi.invoices(id),
    enabled: Boolean(id),
  })
  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['invoice-schedule', id] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-schedule-invoices', id] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-schedule-periods', id] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-schedules'] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-schedule-summary'] })
    void queryClient.invalidateQueries({ queryKey: ['invoices'] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-summary'] })
  }

  const onError = (error: unknown) => {
    const key = invoiceErrorKey(error)
    toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
  }
  const decision = (run: () => Promise<unknown>, successKey: string) => ({
    mutationFn: run,
    onSuccess: () => {
      toast.success(t(successKey))
      refresh()
    },
    onError,
  })

  const pauseMutation = useMutation(decision(() => schedulesApi.pause(id), 'invoices.schedules.paused'))
  const resumeMutation = useMutation(decision(() => schedulesApi.resume(id), 'invoices.schedules.resumed'))
  const generateMutation = useMutation({
    mutationFn: () => schedulesApi.generate(id),
    onSuccess: (emitted) => {
      toast.success(t('invoices.schedules.generated', { count: emitted.length }))
      refresh()
      if (emitted.length === 1) navigate(`/invoices/${emitted[0].id}`)
    },
    onError,
  })
  const deleteMutation = useMutation({
    mutationFn: () => schedulesApi.remove(id),
    onSuccess: () => {
      toast.success(t('invoices.schedules.deleted'))
      refresh()
      navigate('/invoices/schedules')
    },
    onError,
  })
  const removeTermMutation = useMutation({
    mutationFn: (termId: string) => schedulesApi.removeTerm(id, termId),
    onSuccess: () => {
      toast.success(t('invoices.schedules.termRemoved'))
      refresh()
    },
    onError,
  })

  const sortedTerms = useMemo(
    () => (schedule ? [...schedule.terms].sort((a, b) => a.effective_from.localeCompare(b.effective_from)) : []),
    [schedule],
  )

  if (isLoading || !schedule) {
    return (
      <div>
        <Skeleton className="h-4 w-28 mb-6" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    )
  }

  const actions = scheduleActions(schedule)
  // An ended agreement with invoices under it has no decision left to
  // take, and an empty menu is worse than none.
  const hasMenu =
    actions.canEdit || actions.canPause || actions.canResume || actions.canEnd || actions.canDelete
  const money = (value: string | number | null | undefined) =>
    mask(formatCurrency(Number(value ?? 0), schedule.currency, locale))
  const showDate = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(dateLocale)
  const currentId = schedule.current_term?.id
  // The latest period already billed. A term governing it, or anything
  // before it, is history: the server refuses to change it, so the
  // buttons that would try are not offered.
  const billedUpTo = invoices.reduce<string | null>(
    (latest, inv) => (inv.period_start && (!latest || inv.period_start > latest) ? inv.period_start : latest),
    null,
  )

  return (
    <div>
      <button
        onClick={() => navigate('/invoices/schedules')}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors mb-3"
        data-testid="schedule-back"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {t('invoices.schedules.backToList')}
      </button>

      <PageHeader
        section={schedule.payee?.name ?? t('invoices.noClient')}
        title={schedule.name}
        action={
          canWrite ? (
            <div className="flex flex-wrap items-center gap-2">
              {actions.canChangePrice && (
                <Button size="sm" variant="outline" onClick={() => setTermTarget('new')} data-testid="schedule-change-price">
                  <Pencil className="h-4 w-4 mr-1.5" />
                  {t('invoices.schedules.action.changePrice')}
                </Button>
              )}
              {actions.canGenerate && (
                <Button
                  size="sm"
                  onClick={() => generateMutation.mutate()}
                  disabled={generateMutation.isPending}
                  data-testid="schedule-generate"
                >
                  <Zap className="h-4 w-4 mr-1.5" />
                  {t('invoices.schedules.action.generate')}
                </Button>
              )}
              {hasMenu && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    aria-label={t('invoices.moreActions')}
                    data-testid="schedule-more-actions"
                    className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border/80 bg-card text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <MoreHorizontal className="h-4 w-4" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-[220px] p-1 bg-card border border-border rounded-xl shadow-md">
                  {actions.canEdit && (
                    <DropdownMenuItem onClick={() => setEditOpen(true)} data-testid="schedule-edit" className="gap-2 text-sm">
                      <Pencil className="h-4 w-4 text-muted-foreground" />
                      {t('common.edit')}
                    </DropdownMenuItem>
                  )}
                  {actions.canPause && (
                    <DropdownMenuItem onClick={() => pauseMutation.mutate()} data-testid="schedule-pause" className="gap-2 text-sm">
                      <Pause className="h-4 w-4 text-muted-foreground" />
                      {t('invoices.schedules.action.pause')}
                    </DropdownMenuItem>
                  )}
                  {actions.canResume && (
                    <DropdownMenuItem onClick={() => resumeMutation.mutate()} data-testid="schedule-resume" className="gap-2 text-sm">
                      <Play className="h-4 w-4 text-muted-foreground" />
                      {t('invoices.schedules.action.resume')}
                    </DropdownMenuItem>
                  )}
                  {actions.canEnd && (
                    <DropdownMenuItem onClick={() => setEndOpen(true)} data-testid="schedule-end" className="gap-2 text-sm text-destructive focus:text-destructive">
                      <Ban className="h-4 w-4" />
                      {t('invoices.schedules.action.end')}
                    </DropdownMenuItem>
                  )}
                  {actions.canDelete && (
                    <DropdownMenuItem onClick={() => deleteMutation.mutate()} data-testid="schedule-delete" className="gap-2 text-sm text-destructive focus:text-destructive">
                      <Trash2 className="h-4 w-4" />
                      {t('common.delete')}
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
              )}
            </div>
          ) : undefined
        }
      />

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 mb-5 text-xs text-muted-foreground">
        <ScheduleBadge schedule={schedule} />
        <span className="inline-flex items-center gap-1">
          <Repeat className="h-3 w-3" />
          {t(`invoices.schedules.frequency.${schedule.frequency}`)}
        </span>
        <span>{t('invoices.schedules.since', { date: showDate(schedule.start_date) })}</span>
        {schedule.end_type === 'on_date' && schedule.end_date && (
          <span>{t('invoices.schedules.untilDate', { date: showDate(schedule.end_date) })}</span>
        )}
        {schedule.end_type === 'after_count' && schedule.end_count && (
          <span>{t('invoices.schedules.untilCount', { count: schedule.end_count })}</span>
        )}
        {schedule.status === 'ended' && schedule.ended_at && schedule.end_reason && (
          <span data-testid="schedule-ended-line">
            {t('invoices.schedules.endedOn', {
              date: showDate(schedule.ended_at),
              reason: t(`invoices.schedules.endReason.${schedule.end_reason}`),
            })}
          </span>
        )}
        {schedule.status === 'paused' && schedule.pause_reason === 'failures' && (
          <span className="text-rose-500 font-medium">{t('invoices.schedules.pausedByFailures')}</span>
        )}
      </div>

      <div className="space-y-5">
        <SectionCard>
          <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-border">
            {[
              { label: t('invoices.schedules.column.monthly'), value: money(schedule.monthly_amount) },
              { label: t('invoices.schedules.figure.invoiced'), value: money(schedule.amount_invoiced) },
              { label: t('invoices.schedules.figure.paid'), value: money(schedule.amount_paid) },
              {
                label: t('invoices.schedules.column.next'),
                value:
                  schedule.status === 'active' && schedule.next_period_start
                    ? showDate(schedule.next_period_start)
                    : '—',
                tone: schedule.past_due_count > 0 ? 'text-amber-600' : undefined,
                hint:
                  schedule.past_due_count > 0
                    ? t('invoices.schedules.figure.pastDue', { count: schedule.past_due_count })
                    : undefined,
              },
            ].map((figure) => (
              <div key={figure.label} className="px-4 sm:px-5 py-4">
                <p className="text-xs font-medium text-muted-foreground mb-0.5">{figure.label}</p>
                <p className={cn('text-lg font-semibold tracking-tight tabular-nums', 'tone' in figure && figure.tone)}>
                  {figure.value}
                </p>
                {'hint' in figure && figure.hint && (
                  <p className="text-[11px] text-amber-600">{figure.hint}</p>
                )}
              </div>
            ))}
          </div>
        </SectionCard>

        <SectionCard>
          <SectionHeader
            title={t('invoices.schedules.terms.title')}
            description={t('invoices.schedules.terms.description')}
          />
          <div className="divide-y divide-border" data-testid="schedule-terms">
            {sortedTerms.map((term) => {
              const upcoming = isUpcomingTerm(term.effective_from)
              const editable = upcoming && (!billedUpTo || term.effective_from > billedUpTo)
              const isCurrent = term.id === currentId
              return (
                <div key={term.id} className="flex items-center gap-3 px-4 sm:px-5 py-3" data-testid="schedule-term-row">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium tabular-nums">
                        {t('invoices.schedules.terms.from', { date: showDate(term.effective_from) })}
                      </span>
                      {isCurrent && (
                        <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full border bg-emerald-50 text-emerald-600 border-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/20">
                          {t('invoices.schedules.terms.current')}
                        </span>
                      )}
                      {upcoming && (
                        <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full border bg-muted text-muted-foreground border-border">
                          {t('invoices.schedules.terms.upcoming')}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground truncate">
                      {term.lines.map((line) => line.description).join(' · ')}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="text-sm font-bold tabular-nums">{money(term.total)}</p>
                    <p className="text-[11px] text-muted-foreground tabular-nums">
                      {t('invoices.schedules.terms.perMonth', {
                        amount: money(monthlyEquivalent(Number(term.total), schedule.frequency)),
                      })}
                    </p>
                  </div>
                  {canWrite && editable && schedule.status !== 'ended' && (
                    <div className="flex items-center gap-1">
                      <IconAction onClick={() => setTermTarget(term)} label={t('common.edit')}>
                        <Pencil className="h-3.5 w-3.5" />
                      </IconAction>
                      {sortedTerms.length > 1 && (
                        <IconAction onClick={() => removeTermMutation.mutate(term.id)} label={t('common.delete')}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </IconAction>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </SectionCard>

        <SectionCard>
          <SectionHeader
            title={t('invoices.schedules.invoices.title')}
            description={t('invoices.schedules.invoices.description')}
            action={
              canWrite && schedule.status !== 'ended' ? (
                <Button size="sm" variant="outline" onClick={() => setLinkOpen(true)} data-testid="schedule-link-invoice">
                  <Link2 className="h-4 w-4 mr-1.5" />
                  {t('invoices.schedules.action.linkInvoice')}
                </Button>
              ) : undefined
            }
          />
          {invoices.length === 0 ? (
            <div className="px-5 py-10 text-center" data-testid="schedule-invoices-empty">
              <Receipt className="h-7 w-7 mx-auto text-muted-foreground/50" />
              <p className="mt-3 text-sm text-muted-foreground max-w-sm mx-auto">
                {schedule.status === 'active' && schedule.next_period_start
                  ? t('invoices.schedules.invoices.emptyNext', { date: showDate(schedule.next_period_start) })
                  : t('invoices.schedules.invoices.empty')}
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border">
                    <th className={`${TH} pl-4 sm:pl-5 text-left`}>{t('invoices.schedules.column.period')}</th>
                    <th className={`${TH} text-left w-24 hidden sm:table-cell`}>{t('invoices.column.number')}</th>
                    <th className={`${TH} text-left w-32 hidden md:table-cell`}>{t('invoices.column.due')}</th>
                    <th className={`${TH} text-right w-32`}>{t('invoices.column.total')}</th>
                    <th className={`${TH} pr-4 sm:pr-5 text-right w-28`}>{t('invoices.column.state')}</th>
                  </tr>
                </thead>
                <tbody>
                  {[...invoices]
                    .sort((a, b) => (b.sequence ?? 0) - (a.sequence ?? 0))
                    .map((invoice) => (
                      <tr
                        key={invoice.id}
                        onClick={() => navigate(`/invoices/${invoice.id}`)}
                        data-testid="schedule-invoice-row"
                        className="border-b border-border last:border-0 hover:bg-muted transition-colors cursor-pointer"
                      >
                        <td className="py-3 pl-4 sm:pl-5 text-sm tabular-nums">
                          {invoice.period_start && invoice.period_end
                            ? periodLabel(invoice.period_start, invoice.period_end, dateLocale)
                            : showDate(invoice.issue_date)}
                        </td>
                        <td className="py-3 text-xs text-muted-foreground tabular-nums hidden sm:table-cell">
                          {displayNumber(invoice, settings?.number_prefix) ?? t('invoices.noNumber')}
                        </td>
                        <td className="py-3 text-xs text-muted-foreground tabular-nums hidden md:table-cell">
                          {showDate(invoice.due_date)}
                        </td>
                        <td className="py-3 text-right text-sm tabular-nums">{money(invoice.total)}</td>
                        <td className="py-3 pr-4 sm:pr-5 text-right">
                          <StateBadge state={invoice.state} />
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </SectionCard>
      </div>

      <EditScheduleDialog
        key={editOpen ? 'open' : 'closed'}
        open={editOpen}
        onOpenChange={setEditOpen}
        schedule={schedule}
        onSaved={refresh}
      />
      <EndScheduleDialog
        key={endOpen ? 'end-open' : 'end-closed'}
        open={endOpen}
        onOpenChange={setEndOpen}
        schedule={schedule}
        onEnded={refresh}
      />
      <TermDialog
        key={termTarget === null ? 'term-closed' : termTarget === 'new' ? 'term-new' : termTarget.id}
        open={termTarget !== null}
        onOpenChange={(open) => !open && setTermTarget(null)}
        schedule={schedule}
        term={termTarget === 'new' ? null : termTarget}
        onSaved={refresh}
      />
      <LinkInvoiceDialog
        key={linkOpen ? 'link-open' : 'link-closed'}
        open={linkOpen}
        onOpenChange={setLinkOpen}
        schedule={schedule}
        onLinked={refresh}
      />
    </div>
  )
}

function useScheduleError() {
  const { t } = useTranslation()
  return (error: unknown) => {
    const key = invoiceErrorKey(error)
    toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
  }
}

function EditScheduleDialog({
  open,
  onOpenChange,
  schedule,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  schedule: InvoiceSchedule
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const onError = useScheduleError()
  const { data: clients = [] } = useQuery({
    queryKey: ['payees', 'for-invoice'],
    queryFn: () => payeesApi.list({}),
    enabled: open,
  })
  const [name, setName] = useState(schedule.name)
  const [payeeId, setPayeeId] = useState(schedule.payee_id ?? '')
  const [frequency, setFrequency] = useState<InvoiceScheduleFrequency>(schedule.frequency)
  const [startDate, setStartDate] = useState(schedule.start_date)
  const [endType, setEndType] = useState<InvoiceScheduleEndType>(schedule.end_type)
  const [endDate, setEndDate] = useState(schedule.end_date ?? '')
  const [endCount, setEndCount] = useState(schedule.end_count ? String(schedule.end_count) : '')
  const [paymentTerms, setPaymentTerms] = useState(
    schedule.payment_terms_days === null ? '' : String(schedule.payment_terms_days),
  )
  const [notes, setNotes] = useState(schedule.notes ?? '')
  // The calendar is fixed once anything was billed: moving the anchor
  // would change what period every existing invoice was for.
  const calendarLocked = schedule.invoice_count > 0

  const mutation = useMutation({
    mutationFn: () =>
      schedulesApi.update(schedule.id, {
        name,
        payee_id: payeeId || null,
        ...(calendarLocked ? {} : { frequency, start_date: startDate }),
        ...endPayload(endType, endDate, endCount),
        payment_terms_days: paymentTerms ? Number(paymentTerms) : null,
        notes: notes || null,
      }),
    onSuccess: () => {
      toast.success(t('invoices.schedules.saved'))
      onOpenChange(false)
      onSaved()
    },
    onError,
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('invoices.schedules.editTitle')}</DialogTitle>
          <DialogDescription>
            {calendarLocked
              ? t('invoices.schedules.editLockedDescription')
              : t('invoices.schedules.editDescription')}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="edit-schedule-name">{t('invoices.schedules.field.name')}</Label>
            <Input id="edit-schedule-name" data-testid="edit-schedule-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label>{t('invoices.field.client')}</Label>
            <Select value={payeeId} onValueChange={setPayeeId}>
              <SelectTrigger data-testid="edit-schedule-client">
                <SelectValue placeholder={t('invoices.field.clientPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {clients.map((client) => (
                  <SelectItem key={client.id} value={client.id}>{client.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <Label>{t('invoices.schedules.field.frequency')}</Label>
              <Select value={frequency} onValueChange={(v) => setFrequency(v as InvoiceScheduleFrequency)} disabled={calendarLocked}>
                <SelectTrigger data-testid="edit-schedule-frequency"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {FREQUENCIES.map((value) => (
                    <SelectItem key={value} value={value}>{t(`invoices.schedules.frequency.${value}`)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edit-schedule-start">{t('invoices.schedules.field.startDate')}</Label>
              <Input id="edit-schedule-start" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} disabled={calendarLocked} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edit-schedule-terms">{t('invoices.schedules.field.paymentTerms')}</Label>
              <Input id="edit-schedule-terms" type="number" min={0} value={paymentTerms} onChange={(e) => setPaymentTerms(e.target.value)} />
            </div>
          </div>
          <EndConditionFields
            idPrefix="edit-schedule"
            endType={endType}
            endDate={endDate}
            endCount={endCount}
            onChange={(next) => { setEndType(next.endType); setEndDate(next.endDate); setEndCount(next.endCount) }}
          />
          <div className="space-y-1.5">
            <Label htmlFor="edit-schedule-notes">{t('invoices.field.notes')}</Label>
            <Input id="edit-schedule-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button onClick={() => mutation.mutate()} disabled={!name.trim() || mutation.isPending} data-testid="edit-schedule-save">
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

const END_REASONS: InvoiceScheduleEndReason[] = ['canceled_by_client', 'canceled_by_us', 'completed', 'unpaid', 'other']

function EndScheduleDialog({
  open,
  onOpenChange,
  schedule,
  onEnded,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  schedule: InvoiceSchedule
  onEnded: () => void
}) {
  const { t } = useTranslation()
  const onError = useScheduleError()
  const [reason, setReason] = useState<InvoiceScheduleEndReason>('canceled_by_client')
  const [endedAt, setEndedAt] = useState(() => localToday())
  const mutation = useMutation({
    mutationFn: () => schedulesApi.end(schedule.id, { reason, ended_at: endedAt }),
    onSuccess: () => {
      toast.success(t('invoices.schedules.ended'))
      onOpenChange(false)
      onEnded()
    },
    onError,
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('invoices.schedules.endTitle')}</DialogTitle>
          {/* Why it ended is the one fact no invoice can record, and the
              whole reason this is a dialog rather than a button. */}
          <DialogDescription>{t('invoices.schedules.endDescription')}</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label>{t('invoices.schedules.field.endReason')}</Label>
            <Select value={reason} onValueChange={(v) => setReason(v as InvoiceScheduleEndReason)}>
              <SelectTrigger data-testid="end-schedule-reason"><SelectValue /></SelectTrigger>
              <SelectContent>
                {END_REASONS.map((value) => (
                  <SelectItem key={value} value={value}>{t(`invoices.schedules.endReason.${value}`)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="end-schedule-date">{t('invoices.schedules.field.endedAt')}</Label>
            <Input id="end-schedule-date" data-testid="end-schedule-date" type="date" value={endedAt} onChange={(e) => setEndedAt(e.target.value)} />
          </div>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button variant="destructive" onClick={() => mutation.mutate()} disabled={mutation.isPending} data-testid="end-schedule-confirm">
            {t('invoices.schedules.action.end')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function TermDialog({
  open,
  onOpenChange,
  schedule,
  term,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  schedule: InvoiceSchedule
  /** Null creates a new term: the "change price" door. */
  term: InvoiceScheduleTerm | null
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const onError = useScheduleError()
  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
    enabled: open,
  })
  const source = term ?? schedule.next_term ?? schedule.current_term
  const [effectiveFrom, setEffectiveFrom] = useState(
    term?.effective_from ?? schedule.next_period_start ?? localToday(),
  )
  const [lines, setLines] = useState<InvoiceLineInput[]>(
    source ? source.lines.map((line) => ({ ...line })) : [{ description: '', quantity: '1', unit_price: '0' }],
  )
  const [discount, setDiscount] = useState(source?.discount ?? '')

  const perPeriod = linesTotal(lines) - Number(discount || 0)
  const mutation = useMutation({
    mutationFn: () => {
      const payload = { effective_from: effectiveFrom, lines, discount: discount || null }
      return term
        ? schedulesApi.updateTerm(schedule.id, term.id, payload)
        : schedulesApi.addTerm(schedule.id, payload)
    },
    onSuccess: () => {
      toast.success(t('invoices.schedules.termSaved'))
      onOpenChange(false)
      onSaved()
    },
    onError,
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex flex-col max-h-[calc(100dvh-2rem)] sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            {term ? t('invoices.schedules.terms.editTitle') : t('invoices.schedules.action.changePrice')}
          </DialogTitle>
          {/* No proration, by design: the change applies from the first
              period starting on or after the date. The description says
              so, because the alternative is someone expecting a mid-month
              difference to appear on its own. */}
          <DialogDescription>{t('invoices.schedules.terms.changeDescription')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="term-from">{t('invoices.schedules.terms.effectiveFrom')}</Label>
              <Input id="term-from" data-testid="term-from-input" type="date" value={effectiveFrom} onChange={(e) => setEffectiveFrom(e.target.value)} />
              {schedule.next_period_start && (
                <p className="text-[11px] text-muted-foreground">
                  {t('invoices.schedules.terms.nextPeriodHint', {
                    date: new Date(`${schedule.next_period_start}T00:00:00`).toLocaleDateString(dateLocale),
                  })}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="term-discount">{t('invoices.schedules.terms.discount')}</Label>
              <Input id="term-discount" data-testid="term-discount-input" inputMode="decimal" value={discount} onChange={(e) => setDiscount(e.target.value)} placeholder="0.00" />
            </div>
          </div>
          <InvoiceLineEditor
            lines={lines}
            onChange={setLines}
            currency={schedule.currency}
            showTax={(settings?.tax_fields ?? 'hidden') !== 'hidden'}
            required
          />
          <p className="text-xs text-muted-foreground" data-testid="term-monthly-preview">
            {t('invoices.schedules.terms.preview', {
              period: formatCurrency(perPeriod, schedule.currency, locale),
              monthly: formatCurrency(monthlyEquivalent(perPeriod, schedule.frequency), schedule.currency, locale),
            })}
          </p>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button onClick={() => mutation.mutate()} disabled={perPeriod <= 0 || !effectiveFrom || mutation.isPending} data-testid="term-save">
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function LinkInvoiceDialog({
  open,
  onOpenChange,
  schedule,
  onLinked,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  schedule: InvoiceSchedule
  onLinked: () => void
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const onError = useScheduleError()
  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
    enabled: open,
  })
  const { data: periods = [] } = useQuery({
    queryKey: ['invoice-schedule-periods', schedule.id],
    queryFn: () => schedulesApi.periods(schedule.id, 3),
    enabled: open,
  })
  // Same client, same currency, not yet part of any agreement: the
  // only invoices that could answer for a period of this one.
  const { data: candidates = [] } = useQuery({
    queryKey: ['invoices', 'linkable', schedule.id],
    queryFn: () =>
      invoicesApi.list({
        ...(schedule.payee_id ? { payee_id: schedule.payee_id } : {}),
        limit: 500,
      }),
    select: (rows) =>
      rows.filter(
        (invoice) =>
          !invoice.schedule_id && invoice.status !== 'void' && invoice.currency === schedule.currency,
      ),
    enabled: open,
  })
  const [invoiceId, setInvoiceId] = useState('')
  const [periodStart, setPeriodStart] = useState('')
  const free = periods.filter((p) => !p.taken)

  const mutation = useMutation({
    mutationFn: () => schedulesApi.link(schedule.id, { invoice_id: invoiceId, period_start: periodStart }),
    onSuccess: () => {
      toast.success(t('invoices.schedules.linked'))
      onOpenChange(false)
      onLinked()
    },
    onError,
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('invoices.schedules.linkTitle')}</DialogTitle>
          <DialogDescription>{t('invoices.schedules.linkDescription')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>{t('invoices.schedules.field.invoice')}</Label>
            <Select value={invoiceId} onValueChange={setInvoiceId}>
              <SelectTrigger data-testid="link-invoice-select">
                <SelectValue placeholder={t('invoices.schedules.field.invoicePlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {candidates.map((invoice) => (
                  <SelectItem key={invoice.id} value={invoice.id}>
                    {(displayNumber(invoice, settings?.number_prefix) ?? t('invoices.noNumber')) +
                      ' · ' +
                      new Date(`${invoice.issue_date}T00:00:00`).toLocaleDateString(dateLocale) +
                      ' · ' +
                      mask(formatCurrency(Number(invoice.total), invoice.currency, locale))}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {candidates.length === 0 && (
              <p className="text-[11px] text-muted-foreground">{t('invoices.schedules.noCandidates')}</p>
            )}
          </div>
          <div className="space-y-1.5">
            <Label>{t('invoices.schedules.field.period')}</Label>
            <Select value={periodStart} onValueChange={setPeriodStart}>
              <SelectTrigger data-testid="link-period-select">
                <SelectValue placeholder={t('invoices.schedules.field.periodPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {free.map((period) => (
                  <SelectItem key={period.sequence} value={period.period_start}>
                    {periodLabel(period.period_start, period.period_end, dateLocale)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button onClick={() => mutation.mutate()} disabled={!invoiceId || !periodStart || mutation.isPending} data-testid="link-invoice-submit">
            <Plus className="h-4 w-4 mr-1.5" />
            {t('invoices.schedules.action.link')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
