import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Calendar as CalendarIcon,
  ChevronDown,
  Plus,
  Receipt,
  Settings2,
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { PageHeader } from '@/components/page-header'
import { SectionCard, Segmented, StateBadge, TH } from '@/components/invoice-ui'
import { InvoiceLineEditor } from '@/components/invoice-line-editor'
import { InvoiceLogoField } from '@/components/invoice-logo-field'
import { CurrencySelect } from '@/components/currency-select'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { fiscal as fiscalApi, invoices as invoicesApi, payees as payeesApi } from '@/lib/api'
import {
  customFieldDefs,
  displayNumber,
  invoiceErrorKey,
  linesTotal,
} from '@/lib/invoice-utils'
import type {
  Invoice,
  InvoiceDirection,
  InvoiceLineInput,
  InvoiceTemplate,
  IssuerTaxId,
} from '@/types'

/**
 * Receivables: what is owed, what is late, what landed.
 *
 * Reachable only from a business workspace — the module resolver leaves
 * `invoices` out of a personal one, so nothing here is ever rendered
 * there.
 */

type Filter = 'all' | 'open' | 'overdue' | 'paid' | 'draft'

/** The three derived states where money is still expected. `open` in the
 *  filter bar means all of them, which is why it is not a server query. */
const OUTSTANDING: string[] = ['open', 'partial', 'overdue']

/** Aging buckets, oldest last. The tone runs from quiet to loud with the
 *  age, so the bar reads as a temperature without needing its legend. */
const BUCKETS = [
  { key: 'current', tone: 'bg-emerald-500/70' },
  { key: 'd1_30', tone: 'bg-amber-400/80' },
  { key: 'd31_60', tone: 'bg-orange-500/80' },
  { key: 'd61_90', tone: 'bg-rose-500/80' },
  { key: 'd90_plus', tone: 'bg-rose-700/80' },
] as const

export default function InvoicesPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const currency = user?.preferences?.currency_display ?? 'USD'

  // `null` is "every year". The default is the current one, which is
  // what someone opening the page is almost always asking about.
  // Which ledger is on screen. A bigger axis than the state filter — the
  // totals above the list change with it — so it sits above the summary
  // rather than among the filters.
  const [direction, setDirection] = useState<InvoiceDirection>('receivable')
  const [year, setYear] = useState<number | null>(() => new Date().getFullYear())
  const [filter, setFilter] = useState<Filter>('all')
  const [createOpen, setCreateOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)

  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
  })
  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['invoice-summary', direction],
    queryFn: () => invoicesApi.summary(direction),
  })
  const { data: facets } = useQuery({
    queryKey: ['invoice-facets', year, direction],
    queryFn: () => invoicesApi.facets(year ?? undefined, direction),
  })
  const { data: list, isLoading } = useQuery({
    queryKey: ['invoices', filter, year, direction],
    queryFn: () =>
      invoicesApi.list({
        direction,
        // `open` spans three derived states, so it is filtered here
        // rather than asked for three times.
        ...(filter === 'all' || filter === 'open' ? {} : { state: filter }),
        ...(year ? { year } : {}),
      }),
  })

  const visible = useMemo(() => {
    if (!list) return []
    return filter === 'open' ? list.filter((i) => OUTSTANDING.includes(i.state)) : list
  }, [list, filter])

  const money = (value: string | number | null | undefined, code?: string) =>
    mask(formatCurrency(Number(value ?? 0), code ?? currency, locale))

  const showDate = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString(dateLocale)

  const outstanding = Number(summary?.outstanding ?? 0)
  const overdue = Number(summary?.overdue_amount ?? 0)
  const bucketTotal = BUCKETS.reduce(
    (sum, b) => sum + Number(summary?.buckets[b.key] ?? 0),
    0,
  )

  return (
    <div>
      <PageHeader
        section={t('invoices.section')}
        title={t('invoices.title')}
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setSettingsOpen(true)}
              data-testid="invoice-settings-button"
            >
              <Settings2 className="h-4 w-4 mr-1.5" />
              {t('invoices.settings.title')}
            </Button>
            {canWrite && (
              <Button size="sm" onClick={() => setCreateOpen(true)} data-testid="invoice-new-button">
                <Plus className="h-4 w-4 mr-1.5" />
                {direction === 'payable' ? t('invoices.newPayable') : t('invoices.new')}
              </Button>
            )}
          </div>
        }
      />

      <div className="mb-4">
        <Segmented<InvoiceDirection>
          value={direction}
          onChange={setDirection}
          testIdPrefix="invoice-direction"
          options={[
            { value: 'receivable', label: t('invoices.direction.receivable') },
            { value: 'payable', label: t('invoices.direction.payable') },
          ]}
        />
      </div>

      {/* One block, in the dashboard's shape: a headline figure with its
          supporting numbers beside it, and the accountant's view of the
          same money to the right. Four identical stat cards say less. */}
      <div className="bg-card rounded-xl border border-border shadow-sm mb-5">
        <div className="grid grid-cols-1 lg:grid-cols-3">
          <div className="lg:col-span-2 px-5 py-4">
            <p className="text-xs font-medium text-muted-foreground mb-0.5">
              {direction === 'payable'
                ? t('invoices.summary.owed')
                : t('invoices.summary.outstanding')}
            </p>
            {summaryLoading ? (
              <Skeleton className="h-10 w-40" />
            ) : (
              <p className="text-4xl font-bold tabular-nums leading-tight text-foreground">
                {money(outstanding)}
              </p>
            )}

            <div className="flex flex-wrap gap-6 mt-4">
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-0.5">
                  {t('invoices.summary.overdue')}
                </p>
                <p
                  className={cn(
                    'text-sm font-bold tabular-nums',
                    overdue > 0 ? 'text-rose-500' : 'text-muted-foreground',
                  )}
                  data-testid="summary-overdue"
                >
                  {money(overdue)}
                  {summary && summary.overdue_count > 0 && (
                    <span className="ml-1.5 font-medium text-muted-foreground">
                      {t('invoices.summary.overdueCount', { count: summary.overdue_count })}
                    </span>
                  )}
                </p>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-0.5">
                  {direction === 'payable'
                    ? t('invoices.summary.paidThisMonth')
                    : t('invoices.summary.receivedThisMonth')}
                </p>
                <p className="text-sm font-bold tabular-nums text-emerald-600" data-testid="summary-received">
                  {money(summary?.received_this_month)}
                </p>
              </div>
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-0.5">
                  {t('invoices.summary.upcoming')}
                </p>
                <p className="text-sm font-bold tabular-nums text-foreground" data-testid="summary-upcoming">
                  {summary?.upcoming.length ?? 0}
                </p>
              </div>
            </div>
          </div>

          <div className="px-5 py-4 border-t border-border lg:border-t-0 lg:border-l">
            <p className="text-xs font-medium text-muted-foreground mb-2">
              {t('invoices.summary.aging')}
            </p>
            {bucketTotal > 0 ? (
              <>
                <div
                  className="flex h-2 w-full overflow-hidden rounded-full bg-muted"
                  data-testid="aging-bar"
                >
                  {BUCKETS.map((bucket) => {
                    const amount = Number(summary?.buckets[bucket.key] ?? 0)
                    if (amount <= 0) return null
                    return (
                      <div
                        key={bucket.key}
                        className={bucket.tone}
                        style={{ width: `${(amount / bucketTotal) * 100}%` }}
                        title={t(`invoices.bucket.${bucket.key}`)}
                      />
                    )
                  })}
                </div>
                <dl className="mt-3 space-y-1">
                  {BUCKETS.map((bucket) => {
                    const amount = Number(summary?.buckets[bucket.key] ?? 0)
                    if (amount <= 0) return null
                    return (
                      <div key={bucket.key} className="flex items-center gap-2 text-xs">
                        <span className={cn('h-2 w-2 rounded-full shrink-0', bucket.tone)} />
                        <dt className="text-muted-foreground">
                          {t(`invoices.bucket.${bucket.key}`)}
                        </dt>
                        <dd className="ml-auto tabular-nums font-medium">{money(amount)}</dd>
                      </div>
                    )
                  })}
                </dl>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">{t('invoices.summary.nothingDue')}</p>
            )}
          </div>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Segmented<Filter>
          value={filter}
          onChange={setFilter}
          testIdPrefix="invoice-filter"
          options={(['all', 'open', 'overdue', 'paid', 'draft'] as const).map((value) => ({
            value,
            label: t(`invoices.filter.${value}`),
            count: facets?.counts[value],
          }))}
        />

        {/* The year sits with the list's own controls, not with the
            summary: it scopes what is listed, and the summary above
            deliberately has no year — an unpaid 2024 invoice is still
            owed today. */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            {/* Same trigger the dashboard uses for its period: a
                dropdown rather than the dashboard's arrows because only
                years that have a document are worth stepping to, and
                arrows would walk into empty ones. */}
            <button
              type="button"
              data-testid="invoice-year-trigger"
              className="inline-flex items-center justify-center gap-2 border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground hover:bg-muted/50 transition-all cursor-pointer min-w-[180px]"
            >
              <CalendarIcon className="size-3.5 text-muted-foreground" />
              {year ? t('invoices.fiscalYear', { year }) : t('invoices.allYears')}
              <ChevronDown className="size-3.5 text-muted-foreground ml-auto" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            className="w-[180px] p-1 bg-card border border-border rounded-xl shadow-md"
          >
            <DropdownMenuItem
              onClick={() => setYear(null)}
              data-testid="invoice-year-all"
              className="text-sm"
            >
              {t('invoices.allYears')}
            </DropdownMenuItem>
            {/* Only years that actually have an invoice. Offering an
                empty year is offering a dead end. */}
            {(facets?.years ?? []).map((option) => (
              <DropdownMenuItem
                key={option}
                onClick={() => setYear(option)}
                data-testid={`invoice-year-${option}`}
                className="text-sm tabular-nums"
              >
                {t('invoices.fiscalYear', { year: option })}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <SectionCard>
        {isLoading ? (
          <div className="p-5 space-y-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <div className="px-5 py-14 text-center" data-testid="invoices-empty">
            <Receipt className="h-8 w-8 mx-auto text-muted-foreground/50" />
            <p className="mt-3 text-sm text-muted-foreground max-w-sm mx-auto">
              {filter !== 'all'
                ? t('invoices.emptyFiltered')
                : year
                  ? t('invoices.emptyYear', { year: t('invoices.fiscalYear', { year }) })
                  : t('invoices.empty')}
            </p>
            {/* When a year emptied the list, the useful next move is to
                widen it — not to create an invoice in a year nobody is
                looking at. */}
            {filter === 'all' && year && facets && facets.years.length > 0 ? (
              <Button size="sm" variant="outline" className="mt-4" onClick={() => setYear(null)}>
                {t('invoices.showAllYears')}
              </Button>
            ) : (
              filter === 'all' &&
              canWrite && (
                <Button size="sm" className="mt-4" onClick={() => setCreateOpen(true)}>
                  <Plus className="h-4 w-4 mr-1.5" />
                  {t('invoices.new')}
                </Button>
              )
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border">
                  <th className={`${TH} pl-4 sm:pl-5 text-left`}>
                    {direction === 'payable'
                      ? t('invoices.column.supplier')
                      : t('invoices.column.client')}
                  </th>
                  <th className={`${TH} text-left w-24 hidden sm:table-cell`}>
                    {t('invoices.column.number')}
                  </th>
                  <th className={`${TH} text-left w-32 hidden md:table-cell`}>
                    {t('invoices.column.due')}
                  </th>
                  <th className={`${TH} text-right w-32`}>{t('invoices.column.total')}</th>
                  <th className={`${TH} text-right w-32 hidden sm:table-cell`}>
                    {t('invoices.column.balance')}
                  </th>
                  <th className={`${TH} pr-4 sm:pr-5 text-right w-28`}>
                    {t('invoices.column.state')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((invoice) => (
                  <tr
                    key={invoice.id}
                    onClick={() => navigate(`/invoices/${invoice.id}`)}
                    data-testid="invoice-row"
                    className="border-b border-border last:border-0 hover:bg-muted transition-colors cursor-pointer"
                  >
                    <td className="py-3 pl-4 sm:pl-5">
                      <div className="text-sm font-medium text-foreground truncate">
                        {invoice.payee?.name ?? (
                          <span className="text-muted-foreground">
                            {direction === 'payable'
                              ? t('invoices.noSupplier')
                              : t('invoices.noClient')}
                          </span>
                        )}
                      </div>
                      {/* The number and date fold in here on small screens
                          rather than disappearing with their columns. */}
                      <div className="sm:hidden text-xs text-muted-foreground tabular-nums mt-0.5">
                        {displayNumber(invoice, settings?.number_prefix) ?? t('invoices.noNumber')}
                        {' · '}
                        {showDate(invoice.due_date)}
                      </div>
                    </td>
                    <td className="py-3 text-xs text-muted-foreground tabular-nums hidden sm:table-cell">
                      {displayNumber(invoice, settings?.number_prefix) ?? (
                        <span className="text-muted-foreground/60">{t('invoices.noNumber')}</span>
                      )}
                    </td>
                    <td className="py-3 hidden md:table-cell">
                      <span className="text-xs text-muted-foreground tabular-nums">
                        {showDate(invoice.due_date)}
                      </span>
                      {invoice.days_overdue > 0 && (
                        <span className="ml-1.5 text-[11px] font-medium text-rose-500">
                          {t('invoices.daysLate', { count: invoice.days_overdue })}
                        </span>
                      )}
                    </td>
                    <td className="py-3 text-right text-xs sm:text-sm tabular-nums text-muted-foreground">
                      {money(invoice.total, invoice.currency)}
                    </td>
                    <td className="py-3 text-right text-xs sm:text-sm font-bold tabular-nums hidden sm:table-cell">
                      {Number(invoice.balance) > 0 ? (
                        money(invoice.balance, invoice.currency)
                      ) : (
                        <span className="text-muted-foreground font-medium">—</span>
                      )}
                    </td>
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

      <CreateInvoiceDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        direction={direction}
        onCreated={(invoice) => navigate(`/invoices/${invoice.id}`)}
      />
      {/* Keyed on `open`: the dialog is mounted either way, so without
          this its draft state outlives a Cancel — reopening showed the
          edits back, and Save wrote the change the user had rejected. */}
      <InvoiceSettingsDialog
        key={settingsOpen ? 'open' : 'closed'}
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
      />
    </div>
  )
}

function CreateInvoiceDialog({
  open,
  onOpenChange,
  direction,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Taken from the list rather than asked again: the user already chose
   *  a ledger to be looking at, and asking twice is asking them to repeat
   *  themselves. */
  direction: InvoiceDirection
  onCreated: (invoice: Invoice) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
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

  const [payeeId, setPayeeId] = useState<string>('')
  const [total, setTotal] = useState('')
  const [dueDate, setDueDate] = useState('')
  const [notes, setNotes] = useState('')
  const [custom, setCustom] = useState<Record<string, string>>({})
  const [lines, setLines] = useState<InvoiceLineInput[]>([])

  const defs = customFieldDefs(settings?.template)
  const { user } = useAuth()
  // An invoice is denominated in one currency, and it is not necessarily
  // the one this user reads the app in — a freelancer in São Paulo bills
  // a client in New York in USD and is paid into a BRL account. The
  // display preference is only the starting guess.
  const [currencyCode, setCurrencyCode] = useState(
    user?.preferences?.currency_display ?? 'USD',
  )

  const mutation = useMutation({
    mutationFn: (asDraft: boolean) =>
      invoicesApi.create({
        direction,
        as_draft: asDraft,
        payee_id: payeeId || null,
        // Lines are the source of truth once they exist: the server
        // recomputes the total from them and ignores what was typed.
        ...(lines.length ? { lines } : { total }),
        ...(dueDate ? { due_date: dueDate } : {}),
        currency: currencyCode,
        notes: notes || null,
        ...(Object.keys(custom).length ? { custom_fields: custom } : {}),
      }),
    onSuccess: (invoice) => {
      toast.success(
        invoice.status === 'draft' ? t('invoices.draftSaved') : t('invoices.created'),
      )
      // The dialog owns the mutation, so it owns the invalidation: the
      // parent navigates away and would otherwise leave a stale list
      // behind for whenever the user comes back to it.
      void queryClient.invalidateQueries({ queryKey: ['invoices'] })
      void queryClient.invalidateQueries({ queryKey: ['invoice-summary'] })
      void queryClient.invalidateQueries({ queryKey: ['invoice-facets'] })
      onOpenChange(false)
      setPayeeId('')
      setTotal('')
      setDueDate('')
      setNotes('')
      setCurrencyCode(user?.preferences?.currency_display ?? 'USD')
      setCustom({})
      setLines([])
      onCreated(invoice)
    },
    onError: (error) => {
      const key = invoiceErrorKey(error)
      toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
    },
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Widens once there are line items: a table of five columns in a
          narrow dialog is the cramped row of boxes this used to be. */}
      <DialogContent
        className={cn(
          'flex flex-col max-h-[calc(100dvh-2rem)]',
          lines.length ? 'sm:max-w-3xl' : 'sm:max-w-lg',
        )}
      >
        <DialogHeader>
          <DialogTitle>
            {direction === 'payable' ? t('invoices.newPayable') : t('invoices.new')}
          </DialogTitle>
          {/* Three fields is the whole point under the tracking preset:
              the money is already owed, and the document lives elsewhere. */}
          <DialogDescription>
            {direction === 'payable'
              ? t('invoices.newPayableDescription')
              : t('invoices.newDescription')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>
              {direction === 'payable'
                ? t('invoices.field.supplier')
                : t('invoices.field.client')}
            </Label>
            <Select value={payeeId} onValueChange={setPayeeId}>
              <SelectTrigger data-testid="invoice-client-select">
                <SelectValue
                  placeholder={t(
                    direction === 'payable'
                      ? 'invoices.field.supplierPlaceholder'
                      : 'invoices.field.clientPlaceholder',
                  )}
                />
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

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="invoice-total">{t('invoices.field.total')}</Label>
              <div className="flex gap-2">
                <Input
                  id="invoice-total"
                  data-testid="invoice-total-input"
                  inputMode="decimal"
                  value={lines.length ? linesTotal(lines).toFixed(2) : total}
                  onChange={(e) => setTotal(e.target.value)}
                  // Derived once lines exist, so the two can never disagree.
                  disabled={lines.length > 0}
                  placeholder="0.00"
                />
                <CurrencySelect
                  id="invoice-currency"
                  value={currencyCode}
                  onChange={setCurrencyCode}
                  className="w-28 shrink-0"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invoice-due">{t('invoices.field.dueDate')}</Label>
              <Input
                id="invoice-due"
                data-testid="invoice-due-input"
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
              />
              <p className="text-[11px] text-muted-foreground">
                {t(
                  direction === 'payable'
                    ? 'invoices.field.dueDateHintPayable'
                    : 'invoices.field.dueDateHint',
                  { days: settings?.default_payment_terms_days ?? 30 },
                )}
              </p>
            </div>
          </div>

          {defs.map((def) => (
            <div key={def.key} className="space-y-1.5">
              <Label htmlFor={`custom-${def.key}`}>{def.label}</Label>
              <Input
                id={`custom-${def.key}`}
                data-testid={`invoice-custom-${def.key}`}
                value={custom[def.key] ?? ''}
                onChange={(e) => setCustom({ ...custom, [def.key]: e.target.value })}
              />
            </div>
          ))}

          <InvoiceLineEditor
            lines={lines}
            onChange={setLines}
            currency={currencyCode}
            showTax={(settings?.tax_fields ?? 'hidden') !== 'hidden'}
            // Under the document preset the server requires line items,
            // so the editor opens with an empty row rather than letting
            // the user discover the rule from a rejected submit.
            required={settings?.document_required ?? false}
          />

          <div className="space-y-1.5">
            <Label htmlFor="invoice-notes">{t('invoices.field.notes')}</Label>
            <Input
              id="invoice-notes"
              data-testid="invoice-notes-input"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter className="sm:justify-between gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <div className="flex gap-2">
            {/* A draft costs nothing to leave lying around: it carries no
                number, counts in no total, and is the only state an
                invoice can still be edited in. Under the tracking preset
                the button is the only way to reach it, since that preset
                opens everything on creation. */}
            <Button
              variant="outline"
              onClick={() => mutation.mutate(true)}
              disabled={mutation.isPending}
              data-testid="invoice-save-draft"
            >
              {t('invoices.action.saveDraft')}
            </Button>
            <Button
              onClick={() => mutation.mutate(false)}
              disabled={(lines.length ? linesTotal(lines) <= 0 : !total) || mutation.isPending}
              data-testid="invoice-create-submit"
            >
              {t('common.create')}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function InvoiceSettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
    enabled: open,
  })

  const [draft, setDraft] = useState<Record<string, unknown>>({})
  const value = <K extends keyof NonNullable<typeof settings>>(key: K) =>
    (draft[key as string] as NonNullable<typeof settings>[K]) ?? settings?.[key]

  const mutation = useMutation({
    mutationFn: () => invoicesApi.updateSettings(draft),
    onSuccess: () => {
      toast.success(t('invoices.settings.saved'))
      void queryClient.invalidateQueries({ queryKey: ['invoice-settings'] })
      setDraft({})
      onOpenChange(false)
    },
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Capped and scrolled internally, the way every other tall dialog
          in the app is: a modal taller than the viewport pushes its own
          close button off screen, which is how someone ends up trapped
          in it. Header and footer stay put; only the body moves. */}
      <DialogContent className="sm:max-w-3xl flex flex-col max-h-[calc(100dvh-2rem)]">
        <DialogHeader>
          <DialogTitle>{t('invoices.settings.title')}</DialogTitle>
          <DialogDescription>{t('invoices.settings.description')}</DialogDescription>
        </DialogHeader>

        <div className="overflow-y-auto flex-1 -mx-1 px-1 grid gap-x-8 gap-y-5 sm:grid-cols-2">
          <div className="space-y-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t('invoices.settings.groupBehaviour')}
          </p>
          <div className="space-y-1.5">
            <Label>{t('invoices.settings.preset')}</Label>
            <Select
              value={String(value('preset') ?? 'tracking')}
              onValueChange={(v) => setDraft({ ...draft, preset: v })}
            >
              <SelectTrigger data-testid="invoice-preset-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tracking">{t('invoices.settings.presetTracking')}</SelectItem>
                <SelectItem value="document">{t('invoices.settings.presetDocument')}</SelectItem>
              </SelectContent>
            </Select>
            {/* Says what the choice does, because "tracking vs document"
                means nothing until you know which one issues the paper. */}
            <p className="text-[11px] text-muted-foreground">
              {value('preset') === 'document'
                ? t('invoices.settings.presetDocumentHint')
                : t('invoices.settings.presetTrackingHint')}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="settings-prefix">{t('invoices.settings.numberPrefix')}</Label>
              <Input
                id="settings-prefix"
                data-testid="invoice-prefix-input"
                value={String(value('number_prefix') ?? '')}
                onChange={(e) => setDraft({ ...draft, number_prefix: e.target.value })}
                placeholder="FAT-"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="settings-terms">{t('invoices.settings.paymentTerms')}</Label>
              <Input
                id="settings-terms"
                data-testid="invoice-terms-input"
                type="number"
                min={0}
                value={String(value('default_payment_terms_days') ?? 30)}
                onChange={(e) =>
                  setDraft({ ...draft, default_payment_terms_days: Number(e.target.value) })
                }
              />
            </div>
          </div>

          <IssuerSection />
          </div>

          <div className="space-y-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t('invoices.settings.groupDocument')}
          </p>
          <div className="space-y-1.5">
            <Label htmlFor="settings-issuer">{t('invoices.settings.issuerName')}</Label>
            <Input
              id="settings-issuer"
              data-testid="invoice-issuer-input"
              value={String(value('issuer_display_name') ?? '')}
              onChange={(e) => setDraft({ ...draft, issuer_display_name: e.target.value })}
            />
          </div>

          <div className="space-y-1.5">
            <Label>{t('invoices.settings.logo')}</Label>
            <InvoiceLogoField
              logoId={settings?.logo_id ?? null}
              onChanged={() => {
                void queryClient.invalidateQueries({ queryKey: ['invoice-settings'] })
              }}
            />
            {/* The freeze rule, said out loud: people expect a logo change
                to be retroactive, and it deliberately is not. */}
            <p className="text-[11px] text-muted-foreground">{t('invoices.settings.logoHint')}</p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="settings-payment">{t('invoices.settings.paymentDetails')}</Label>
            <Input
              id="settings-payment"
              data-testid="invoice-payment-details-input"
              value={String(value('payment_details') ?? '')}
              onChange={(e) => setDraft({ ...draft, payment_details: e.target.value })}
              placeholder="Pix: …"
            />
            <p className="text-[11px] text-muted-foreground">
              {t('invoices.settings.paymentDetailsHint')}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="settings-accent">{t('invoices.settings.accentColor')}</Label>
            <div className="flex items-center gap-2">
              <input
                id="settings-accent"
                type="color"
                data-testid="invoice-accent-input"
                className="h-9 w-12 rounded border bg-transparent p-1"
                value={String(value('accent_color') ?? '#111827')}
                onChange={(e) => setDraft({ ...draft, accent_color: e.target.value })}
              />
              <span className="font-mono text-xs text-muted-foreground">
                {String(value('accent_color') ?? '#111827')}
              </span>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="settings-footer">{t('invoices.settings.footerNote')}</Label>
            <Input
              id="settings-footer"
              data-testid="invoice-footer-input"
              value={String(value('footer_note') ?? '')}
              onChange={(e) => setDraft({ ...draft, footer_note: e.target.value })}
            />
          </div>

          </div>

          <div className="sm:col-span-2">
          <LabelSection
            template={(value('template') as InvoiceTemplate | null) ?? null}
            onChange={(template) => setDraft({ ...draft, template })}
          />

          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={Object.keys(draft).length === 0 || mutation.isPending}
            data-testid="invoice-settings-save"
          >
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * The workspace describing itself: what appears as the sender on every
 * document issued from now on.
 *
 * Which fiscal documents are offered comes from the workspace's own
 * jurisdiction pack, so a Brazilian workspace is asked for a CNPJ and a
 * German one for a VAT number without this component knowing either
 * exists. It also never *restricts* the choice — a company can hold a
 * document its country's pack never anticipated.
 */
function IssuerSection() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: issuer } = useQuery({ queryKey: ['invoice-issuer'], queryFn: invoicesApi.issuer })
  const { data: kinds } = useQuery({ queryKey: ['tax-id-kinds'], queryFn: fiscalApi.taxIdKinds })

  const [draft, setDraft] = useState<Record<string, string>>({})
  const [docs, setDocs] = useState<IssuerTaxId[] | null>(null)

  const rows = docs ?? issuer?.tax_ids ?? []
  const offered = (kinds?.kinds ?? []).filter((k) => k.offered)

  const mutation = useMutation({
    mutationFn: () =>
      invoicesApi.updateIssuer({
        ...(draft.legal_name !== undefined ? { legal_name: draft.legal_name } : {}),
        ...(draft.address !== undefined ? { address: draft.address } : {}),
        ...(docs ? { tax_ids: docs.filter((d) => d.value.trim()) } : {}),
      }),
    onSuccess: () => {
      toast.success(t('invoices.settings.saved'))
      void queryClient.invalidateQueries({ queryKey: ['invoice-issuer'] })
      setDraft({})
      setDocs(null)
    },
    onError: (error) => {
      const key = invoiceErrorKey(error)
      toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
    },
  })

  return (
    <div className="border-t pt-4 space-y-3" data-testid="invoice-issuer-section">
      <div>
        <Label>{t('invoices.settings.issuer')}</Label>
        <p className="text-[11px] text-muted-foreground">{t('invoices.settings.issuerHint')}</p>
      </div>

      <Input
        data-testid="issuer-legal-name"
        placeholder={t('invoices.settings.legalName')}
        value={draft.legal_name ?? issuer?.legal_name ?? ''}
        onChange={(e) => setDraft({ ...draft, legal_name: e.target.value })}
      />
      <Input
        data-testid="issuer-address"
        placeholder={t('invoices.settings.addressLabel')}
        value={draft.address ?? issuer?.address ?? ''}
        onChange={(e) => setDraft({ ...draft, address: e.target.value })}
      />

      {offered.map((kind) => {
        const existing = rows.find((r) => r.kind === kind.kind)
        return (
          <Input
            key={kind.kind}
            data-testid={`issuer-tax-${kind.kind}`}
            placeholder={t(kind.label_key, kind.kind.toUpperCase())}
            value={existing?.value ?? ''}
            onChange={(e) => {
              const next = rows.filter((r) => r.kind !== kind.kind)
              if (e.target.value.trim()) next.push({ kind: kind.kind, value: e.target.value })
              setDocs(next)
            }}
          />
        )
      })}

      <Button
        size="sm"
        variant="outline"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending || (Object.keys(draft).length === 0 && docs === null)}
        data-testid="issuer-save"
      >
        {t('invoices.settings.saveIssuer')}
      </Button>
    </div>
  )
}

/**
 * Renaming what the document calls each field.
 *
 * Only the fields a sender actually renames are offered. The full set is
 * eighteen, and a settings dialog with eighteen text inputs is a wall
 * nobody reads; the rest stay editable through the API for the rare
 * workspace that wants them.
 *
 * Placeholders show the pack for the workspace's language, so an empty
 * box reads as "this is what it will say" rather than as a missing
 * value. Clearing a box returns that label to the pack.
 */
const EDITABLE_LABELS = [
  'invoice',
  'billTo',
  'from',
  'description',
  'quantity',
  'unitPrice',
  'total',
  'paymentDetails',
  'notes',
] as const

function LabelSection({
  template,
  onChange,
}: {
  template: InvoiceTemplate | null
  onChange: (template: InvoiceTemplate) => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const labels = template?.labels ?? {}

  const set = (key: string, value: string) => {
    const next = { ...labels }
    // An empty box means "use the default", not "print nothing".
    if (value.trim()) next[key] = value
    else delete next[key]
    onChange({ ...(template ?? {}), labels: next })
  }

  return (
    <div className="border-t pt-4">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        data-testid="invoice-labels-toggle"
        className="flex w-full items-center justify-between text-left"
      >
        <div>
          <Label className="cursor-pointer">{t('invoices.settings.labels')}</Label>
          <p className="text-[11px] text-muted-foreground">
            {t('invoices.settings.labelsHint')}
          </p>
        </div>
        <ChevronDown
          className={cn('h-4 w-4 text-muted-foreground transition-transform', open && 'rotate-180')}
        />
      </button>

      {open && (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {EDITABLE_LABELS.map((key) => (
            <Input
              key={key}
              data-testid={`invoice-label-${key}`}
              value={labels[key] ?? ''}
              placeholder={t(`invoices.label.${key}`)}
              onChange={(e) => set(key, e.target.value)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
