import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Ban, Check, CheckCircle2, CircleSlash, Copy, Download, Link2,
  MoreHorizontal, Pencil, RotateCcw, Send, Share2, Trash2, Unlink,
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
import {
  IconAction,
  SectionCard,
  SectionHeader,
  Segmented,
  StateBadge,
} from '@/components/invoice-ui'
import { InvoiceDocumentView } from '@/components/invoice-document'
import { InvoiceDocumentBrowser } from '@/components/invoice-documents'
import { InvoiceLineEditor } from '@/components/invoice-line-editor'
import type { Invoice, InvoiceDirection, InvoiceLineInput } from '@/types'
import { cn } from '@/lib/utils'
import { formatCurrency } from '@/lib/format'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import {
  invoices as invoicesApi,
  payees as payeesApi,
  transactions as transactionsApi,
} from '@/lib/api'
import {
  allocationOrigin,
  availableActions,
  customFieldDefs,
  displayNumber,
  invoiceErrorKey,
  linesTotal,
} from '@/lib/invoice-utils'

/**
 * One invoice: what is owed, the money bound to it, and the document.
 *
 * The tab split is the point. "Details" is the operator's view — the
 * ledger side, where money gets linked. "Document" is what the client
 * receives, rendered from the same structure the PDF is. Mixing the two
 * on one screen is what made the first version feel like neither.
 */
type Tab = 'details' | 'document'

export default function InvoiceDetailPage() {
  const { t } = useTranslation()
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const fallbackCurrency = user?.preferences?.currency_display ?? 'USD'

  const [linkOpen, setLinkOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [tab, setTab] = useState<Tab>('details')
  const [copied, setCopied] = useState(false)

  const { data: invoice, isLoading } = useQuery({
    queryKey: ['invoice', id],
    queryFn: () => invoicesApi.get(id),
    enabled: Boolean(id),
  })
  const { data: settings } = useQuery({
    queryKey: ['invoice-settings'],
    queryFn: invoicesApi.settings,
  })
  // Only when the tab is open: it resolves the snapshot and builds the
  // whole page server-side, and the ledger view has no use for any of it.
  // Fetched here rather than only inside the Documents section: the
  // header has to know whether a real document exists before it offers
  // to download one. Same query key as the section, so this is one
  // request shared through the cache, not two.
  const { data: attachments = [] } = useQuery({
    queryKey: ['invoice-attachments', id],
    queryFn: () => invoicesApi.attachments.list(id!),
    enabled: Boolean(id),
  })
  const hasFiledDocument = attachments.some((a) => a.is_primary)

  const { data: documentPayload } = useQuery({
    queryKey: ['invoice-document', id],
    queryFn: () => invoicesApi.document(id),
    enabled: Boolean(id) && tab === 'document',
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['invoice', id] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-document', id] })
    void queryClient.invalidateQueries({ queryKey: ['invoices'] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-summary'] })
    void queryClient.invalidateQueries({ queryKey: ['invoice-facets'] })
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

  const issueMutation = useMutation(decision(() => invoicesApi.issue(id), 'invoices.issued'))
  const voidMutation = useMutation(decision(() => invoicesApi.void(id), 'invoices.voided'))
  const writeOffMutation = useMutation(
    decision(() => invoicesApi.writeOff(id), 'invoices.writtenOff'),
  )
  const reopenMutation = useMutation(decision(() => invoicesApi.reopen(id), 'invoices.reopened'))
  const deleteMutation = useMutation({
    mutationFn: () => invoicesApi.remove(id),
    onSuccess: () => {
      toast.success(t('invoices.deleted'))
      refresh()
      navigate('/invoices')
    },
    onError,
  })
  const unlinkMutation = useMutation({
    mutationFn: (allocationId: string) => invoicesApi.unallocate(id, allocationId),
    onSuccess: () => {
      toast.success(t('invoices.unlinked'))
      refresh()
    },
    onError,
  })

  // Fetched as a blob rather than opened as a link: the PDF route needs
  // the auth and workspace headers the axios interceptor adds, which a
  // plain anchor would not carry.
  const downloadMutation = useMutation({
    mutationFn: async () => {
      const blob = await invoicesApi.pdf(id)
      const url = URL.createObjectURL(blob)
      const anchor = window.document.createElement('a')
      anchor.href = url
      anchor.download = `${invoice?.number ?? 'invoice'}.pdf`
      anchor.click()
      URL.revokeObjectURL(url)
    },
    onError,
  })

  const shareMutation = useMutation({
    mutationFn: () => invoicesApi.share(id),
    onSuccess: async (link) => {
      const url = `${window.location.origin}${link.path}`
      try {
        await navigator.clipboard.writeText(url)
        setCopied(true)
        setTimeout(() => setCopied(false), 2500)
        toast.success(t('invoices.shareCopied'))
      } catch {
        // A blocked clipboard is not a failed share — the link exists.
        toast.success(url)
      }
      refresh()
    },
    onError,
  })

  const unshareMutation = useMutation({
    mutationFn: () => invoicesApi.unshare(id),
    onSuccess: () => {
      toast.success(t('invoices.shareRevoked'))
      refresh()
    },
    onError,
  })

  if (isLoading || !invoice) {
    return (
      <div>
        <Skeleton className="h-4 w-28 mb-6" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    )
  }

  const actions = availableActions(invoice)
  const currency = invoice.currency || fallbackCurrency
  const money = (value: string | number | null | undefined) =>
    mask(formatCurrency(Number(value ?? 0), currency, locale))
  const showDate = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(dateLocale)
  const number = displayNumber(invoice, settings?.number_prefix)
  const customFields = customFieldDefs(settings?.template)
    .map((def) => ({ ...def, value: invoice.custom_fields?.[def.key] }))
    .filter((field): field is typeof field & { value: string } => Boolean(field.value))
  const shareUrl = invoice.share_token
    ? `${window.location.origin}/i/${invoice.share_token}`
    : null

  return (
    <div>
      <button
        onClick={() => navigate('/invoices')}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors mb-3"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {t('invoices.backToList')}
      </button>

      <PageHeader
        section={
          invoice.payee?.name ??
          t(invoice.direction === 'payable' ? 'invoices.noSupplier' : 'invoices.noClient')
        }
        title={number ?? t('invoices.draftTitle')}
        action={
          canWrite ? (
            <div className="flex flex-wrap items-center gap-2">
              {actions.canEdit && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setEditOpen(true)}
                  data-testid="invoice-edit"
                >
                  <Pencil className="h-4 w-4 mr-1.5" />
                  {t('common.edit')}
                </Button>
              )}
              {actions.canIssue && (
                <Button size="sm" onClick={() => issueMutation.mutate()} data-testid="invoice-issue">
                  <Send className="h-4 w-4 mr-1.5" />
                  {t('invoices.action.issue')}
                </Button>
              )}
              {actions.canAllocate && (
                <Button size="sm" onClick={() => setLinkOpen(true)} data-testid="invoice-link-payment">
                  <Link2 className="h-4 w-4 mr-1.5" />
                  {t('invoices.action.markPaid')}
                </Button>
              )}
              {invoice.status !== 'draft' && (
                <>
                  {/* Downloading an imported invoice with nothing filed
                      would hand over a page we drew for a document
                      somebody else issued — the same invention the
                      Document tab refuses to make. Nothing to download
                      until the real file arrives. */}
                  {(invoice.origin !== 'imported' || hasFiledDocument) && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => downloadMutation.mutate()}
                      disabled={downloadMutation.isPending}
                      data-testid="invoice-download-pdf"
                    >
                      <Download className="h-4 w-4 mr-1.5" />
                      {t('invoices.action.downloadPdf')}
                    </Button>
                  )}
                  {/* Sharing is for sending your invoice to your client.
                      A bill you received belongs to your supplier and has
                      nobody to be sent to. */}
                  {invoice.direction === 'receivable' && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      invoice.share_token ? unshareMutation.mutate() : shareMutation.mutate()
                    }
                    data-testid={invoice.share_token ? 'invoice-unshare' : 'invoice-share'}
                  >
                    {copied ? (
                      <Check className="h-4 w-4 mr-1.5" />
                    ) : (
                      <Share2 className="h-4 w-4 mr-1.5" />
                    )}
                    {invoice.share_token
                      ? t('invoices.action.revokeLink')
                      : t('invoices.action.share')}
                  </Button>
                  )}
                </>
              )}
              {/* The rare and irreversible decisions live behind an
                  overflow menu, with words. Two bare icons side by side
                  were indistinguishable, and giving "void" the same
                  weight as "mark as paid" is how someone voids by
                  reflex. */}
              {(actions.canWriteOff ||
                actions.canReopen ||
                actions.canVoid ||
                actions.canDelete) && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      aria-label={t('invoices.moreActions')}
                      data-testid="invoice-more-actions"
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border/80 bg-card text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                      <MoreHorizontal className="h-4 w-4" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    align="end"
                    className="w-[220px] p-1 bg-card border border-border rounded-xl shadow-md"
                  >
                    {actions.canWriteOff && (
                      <DropdownMenuItem
                        onClick={() => writeOffMutation.mutate()}
                        data-testid="invoice-writeoff"
                        className="gap-2 text-sm"
                      >
                        <Ban className="h-4 w-4 text-muted-foreground" />
                        {t('invoices.action.writeOff')}
                      </DropdownMenuItem>
                    )}
                    {actions.canReopen && (
                      <DropdownMenuItem
                        onClick={() => reopenMutation.mutate()}
                        data-testid="invoice-reopen"
                        className="gap-2 text-sm"
                      >
                        <RotateCcw className="h-4 w-4 text-muted-foreground" />
                        {t('invoices.action.reopen')}
                      </DropdownMenuItem>
                    )}
                    {actions.canVoid && (
                      <DropdownMenuItem
                        onClick={() => voidMutation.mutate()}
                        data-testid="invoice-void"
                        className="gap-2 text-sm text-destructive focus:text-destructive"
                      >
                        <CircleSlash className="h-4 w-4" />
                        {t('invoices.action.void')}
                      </DropdownMenuItem>
                    )}
                    {actions.canDelete && (
                      <DropdownMenuItem
                        onClick={() => deleteMutation.mutate()}
                        data-testid="invoice-delete"
                        className="gap-2 text-sm text-destructive focus:text-destructive"
                      >
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

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 mb-5">
        <Segmented<Tab>
          value={tab}
          onChange={setTab}
          testIdPrefix="invoice-tab"
          options={[
            { value: 'details', label: t('invoices.tab.details') },
            { value: 'document', label: t('invoices.tab.document') },
          ]}
        />
        <StateBadge state={invoice.state} />
        {invoice.days_overdue > 0 && (
          <span className="text-xs font-medium text-rose-500">
            {t('invoices.daysLate', { count: invoice.days_overdue })}
          </span>
        )}
      </div>

      {tab === 'document' ? (
        <div className="space-y-4">
          {shareUrl && (
            <SectionCard>
              <div
                className="flex flex-wrap items-center gap-2 px-4 sm:px-5 py-3 text-xs"
                data-testid="invoice-share-banner"
              >
                <Share2 className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                <span className="text-muted-foreground">{t('invoices.shareActive')}</span>
                <code className="truncate font-mono text-[11px] text-foreground">{shareUrl}</code>
                <div className="ml-auto">
                  <IconAction
                    onClick={() => {
                      void navigator.clipboard.writeText(shareUrl)
                      toast.success(t('invoices.shareCopied'))
                    }}
                    label={t('invoices.shareCopy')}
                  >
                    <Copy className="h-3.5 w-3.5" />
                  </IconAction>
                </div>
              </div>
            </SectionCard>
          )}
          {documentPayload ? (
            // Every page this invoice has, in one browser: the files down
            // the left, the selected one read on the right. Our own
            // render is one entry among them — and not offered at all on
            // an import, where drawing it would invent a document
            // somebody else issued.
            <InvoiceDocumentBrowser
              invoiceId={invoice.id}
              origin={invoice.origin}
              canWrite={canWrite}
              ourPageLabel={number}
              ourPageDate={invoice.issue_date}
              ourPage={<InvoiceDocumentView document={documentPayload} />}
              onChanged={refresh}
            />
          ) : (
            <Skeleton className="h-[520px] w-full rounded-xl" />
          )}
        </div>
      ) : (
        <div className="space-y-5">
          <SectionCard>
            <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-border">
              {[
                { label: t('invoices.column.total'), value: money(invoice.total) },
                {
                  label:
                    invoice.direction === 'payable'
                      ? t('invoices.field.paidOut')
                      : t('invoices.field.paid'),
                  value: money(invoice.amount_paid),
                },
                {
                  label: t('invoices.column.balance'),
                  value: money(invoice.balance),
                  tone:
                    Number(invoice.balance) > 0
                      ? 'text-foreground'
                      : 'text-emerald-600',
                  testId: 'invoice-balance',
                },
                { label: t('invoices.column.due'), value: showDate(invoice.due_date) },
              ].map((figure) => (
                <div key={figure.label} className="px-4 sm:px-5 py-4" data-testid={figure.testId}>
                  <p className="text-xs font-medium text-muted-foreground mb-0.5">{figure.label}</p>
                  <p
                    className={cn(
                      'text-lg font-bold tabular-nums',
                      figure.tone ?? 'text-foreground',
                    )}
                  >
                    {figure.value}
                  </p>
                </div>
              ))}
            </div>
          </SectionCard>

          {(invoice.lines.length > 0 || invoice.notes || customFields.length > 0) && (
            <SectionCard>
              <SectionHeader title={t('invoices.detailsTitle')} />
              <div className="px-4 sm:px-5 py-4 space-y-4">
                {/* Competence only earns a row when it disagrees with the
                    issue date; otherwise it is noise on every invoice. */}
                {invoice.competence_date && invoice.competence_date !== invoice.issue_date && (
                  <p className="text-xs text-muted-foreground" data-testid="invoice-competence">
                    {t('invoices.competenceDiverges', {
                      competence: showDate(invoice.competence_date),
                      issue: showDate(invoice.issue_date),
                    })}
                  </p>
                )}

                {/* Driven by the workspace's definitions, so the label
                    the sender chose is what shows — never the raw key —
                    and a field removed from settings stops appearing. */}
                {customFields.length > 0 && (
                  <div className="flex flex-wrap gap-x-8 gap-y-2">
                    {customFields.map((field) => (
                      <div key={field.key}>
                        <p className="text-xs font-medium text-muted-foreground mb-0.5">
                          {field.label}
                        </p>
                        <p className="text-sm">{field.value}</p>
                      </div>
                    ))}
                  </div>
                )}

                {invoice.lines.length > 0 && (
                  <table className="w-full">
                    <tbody>
                      {invoice.lines.map((line) => (
                        <tr key={line.id} className="border-b border-border last:border-0">
                          <td className="py-2.5 text-sm text-foreground">{line.description}</td>
                          <td className="py-2.5 text-right text-xs text-muted-foreground tabular-nums">
                            {Number(line.quantity)} × {money(line.unit_price)}
                          </td>
                          <td className="py-2.5 text-right text-sm font-medium tabular-nums w-32">
                            {money(line.total)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                {invoice.notes && (
                  <p className="text-sm text-muted-foreground">{invoice.notes}</p>
                )}
              </div>
            </SectionCard>
          )}

          <SectionCard>
            <SectionHeader
              title={t('invoices.payments')}
              action={
                actions.canAllocate && canWrite ? (
                  <Button size="sm" variant="outline" onClick={() => setLinkOpen(true)}>
                    <Link2 className="h-3.5 w-3.5 mr-1.5" />
                    {t('invoices.action.link')}
                  </Button>
                ) : undefined
              }
            />
            {invoice.allocations.length === 0 ? (
              <p
                className="px-4 sm:px-5 py-8 text-center text-sm text-muted-foreground"
                data-testid="invoice-no-payments"
              >
                {t('invoices.noPayments')}
              </p>
            ) : (
              <table className="w-full">
                <tbody>
                  {invoice.allocations.map((allocation) => (
                    <tr
                      key={allocation.id}
                      data-testid="invoice-allocation"
                      className="border-b border-border last:border-0"
                    >
                      <td className="py-3 pl-4 sm:pl-5">
                        <div className="text-sm font-medium text-foreground truncate">
                          {allocation.transaction?.description ?? t('invoices.linkedPayment')}
                        </div>
                        <div className="text-xs text-muted-foreground tabular-nums mt-0.5">
                          {allocation.transaction?.date
                            ? showDate(allocation.transaction.date)
                            : ''}
                          {' · '}
                          {(() => {
                            const origin = allocationOrigin(allocation.method)
                            if (!origin.automatic) return t('invoices.linkedManually')
                            // The strategy id is shown as the title rather
                            // than the label: it is a machine name today
                            // and becomes a readable one when the policy
                            // is fetchable, without this line changing.
                            return (
                              <span title={origin.strategyId ?? undefined}>
                                {t('invoices.linkedAutomatically')}
                              </span>
                            )
                          })()}
                        </div>
                      </td>
                      <td className="py-3 text-right text-sm font-bold tabular-nums text-emerald-600">
                        {money(allocation.amount)}
                      </td>
                      <td className="py-3 pr-4 sm:pr-5 text-right w-16">
                        {canWrite && (
                          <IconAction
                            onClick={() => unlinkMutation.mutate(allocation.id)}
                            label={t('invoices.action.unlink')}
                            destructive
                          >
                            <Unlink className="h-4 w-4" />
                          </IconAction>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </SectionCard>
        </div>
      )}

      <EditDraftDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        invoice={invoice}
        showTax={(settings?.tax_fields ?? 'hidden') !== 'hidden'}
        currency={currency}
        onSaved={refresh}
      />

      <LinkPaymentDialog
        open={linkOpen}
        onOpenChange={setLinkOpen}
        invoiceId={id}
        direction={invoice.direction}
        balance={invoice.balance}
        currency={currency}
        onLinked={refresh}
      />
    </div>
  )
}

function LinkPaymentDialog({
  open,
  onOpenChange,
  invoiceId,
  direction,
  balance,
  currency,
  onLinked,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  invoiceId: string
  direction: InvoiceDirection
  balance: string
  currency: string
  onLinked: () => void
}) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState<string>('')
  const [amount, setAmount] = useState('')

  // Money moving the way this invoice is settled: a receivable by money
  // coming in, a payable by money going out. Asking for credits either
  // way — which this did — means the payment that actually settled a
  // supplier's bill is never in the list, and the bill stays open
  // forever with no way to close it.
  const settlingType = direction === 'payable' ? 'debit' : 'credit'
  const { data } = useQuery({
    queryKey: ['transactions', 'for-invoice', settlingType],
    queryFn: () => transactionsApi.list({ type: settlingType, limit: 50 }),
    enabled: open,
  })

  // Same currency only — the server refuses a cross-currency allocation
  // rather than inventing a rate, so offering one here would only be
  // offering an error.
  const candidates = useMemo(
    () => (data?.items ?? []).filter((tx) => (tx.currency ?? currency) === currency),
    [data, currency],
  )

  const mutation = useMutation({
    mutationFn: () => invoicesApi.allocate(invoiceId, selected, amount || undefined),
    onSuccess: () => {
      toast.success(t('invoices.linked'))
      onOpenChange(false)
      setSelected('')
      setAmount('')
      onLinked()
    },
    onError: (error) => {
      const key = invoiceErrorKey(error)
      toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
    },
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('invoices.action.markPaid')}</DialogTitle>
          <DialogDescription>{t('invoices.linkDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3 max-h-72 overflow-y-auto">
          {candidates.length === 0 && (
            <p className="text-sm text-muted-foreground">{t('invoices.noCandidates')}</p>
          )}
          {candidates.map((tx) => (
            <button
              key={tx.id}
              onClick={() => setSelected(tx.id)}
              data-testid="invoice-candidate"
              className={cn(
                'w-full flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-colors',
                selected === tx.id ? 'border-primary bg-primary/5' : 'hover:bg-muted/40',
              )}
            >
              <div className="min-w-0">
                <div className="text-sm truncate">{tx.description}</div>
                <div className="text-xs text-muted-foreground tabular-nums">{tx.date}</div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-sm tabular-nums">
                  {formatCurrency(Number(tx.amount), tx.currency ?? currency, 'en')}
                </span>
                {selected === tx.id && <CheckCircle2 className="h-4 w-4 text-primary" />}
              </div>
            </button>
          ))}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="allocation-amount">{t('invoices.field.amountToApply')}</Label>
          <Input
            id="allocation-amount"
            data-testid="invoice-allocation-amount"
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder={balance}
          />
          {/* Leaving it blank is the common case — one payment closing one
              invoice should not require typing the number twice. */}
          <p className="text-[11px] text-muted-foreground">{t('invoices.field.amountHint')}</p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={!selected || mutation.isPending}
            data-testid="invoice-allocation-submit"
          >
            {t('invoices.action.link')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * Editing a draft.
 *
 * Only a draft: once issued, the financial substance is frozen and the
 * server refuses the change, because a document that changes after the
 * client received it is not an edit, it is a second document. The button
 * that opens this disappears at the same moment.
 *
 * Notes stay editable after issuance through the detail view, since they
 * are the seller's own record and never left the building.
 */
function EditDraftDialog({
  open,
  onOpenChange,
  invoice,
  showTax,
  currency,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  invoice: Invoice
  showTax: boolean
  currency: string
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const { data: clients = [] } = useQuery({
    queryKey: ['payees', 'for-invoice'],
    queryFn: () => payeesApi.list({}),
    enabled: open,
  })

  // Seeded from the invoice each time the dialog opens, keyed so a
  // reopen after a save starts from what was saved.
  const [payeeId, setPayeeId] = useState(invoice.payee_id ?? '')
  const [total, setTotal] = useState(invoice.total)
  const [dueDate, setDueDate] = useState(invoice.due_date)
  const [notes, setNotes] = useState(invoice.notes ?? '')
  const [lines, setLines] = useState<InvoiceLineInput[]>(() =>
    invoice.lines.map((line) => ({
      description: line.description,
      quantity: String(Number(line.quantity)),
      unit_price: line.unit_price,
      tax_rate: line.tax_rate,
    })),
  )

  const mutation = useMutation({
    mutationFn: () =>
      invoicesApi.update(invoice.id, {
        payee_id: payeeId || null,
        due_date: dueDate,
        notes: notes || null,
        // Lines are the source of truth once they exist: the server
        // recomputes the total from them and ignores what was typed.
        //
        // An empty list is sent when the draft had lines and no longer
        // does, because omitting the key means "leave them alone" — so
        // deleting every row used to save successfully and change
        // nothing, and the rows came back on the next read.
        ...(lines.length
          ? { lines }
          : invoice.lines.length
            ? { lines: [], total }
            : { total }),
      }),
    onSuccess: () => {
      toast.success(t('invoices.updated'))
      onOpenChange(false)
      onSaved()
    },
    onError: (error) => {
      const key = invoiceErrorKey(error)
      toast.error(key ? t(key, t('invoices.errors.generic')) : t('invoices.errors.generic'))
    },
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Same rule as the create dialog: the line-item table needs the
          room, and without it the row overflows and the remove button
          falls off the right edge. */}
      <DialogContent
        className={cn(
          'flex flex-col max-h-[calc(100dvh-2rem)]',
          lines.length ? 'sm:max-w-3xl' : 'sm:max-w-lg',
        )}
      >
        <DialogHeader>
          <DialogTitle>{t('invoices.editDraft')}</DialogTitle>
          <DialogDescription>{t('invoices.editDraftDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label>{t('invoices.field.client')}</Label>
            <Select value={payeeId} onValueChange={setPayeeId}>
              <SelectTrigger data-testid="edit-client-select">
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

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="edit-total">{t('invoices.field.total')}</Label>
              <Input
                id="edit-total"
                data-testid="edit-total-input"
                inputMode="decimal"
                value={lines.length ? linesTotal(lines).toFixed(2) : total}
                onChange={(e) => setTotal(e.target.value)}
                disabled={lines.length > 0}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edit-due">{t('invoices.field.dueDate')}</Label>
              <Input
                id="edit-due"
                data-testid="edit-due-input"
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
              />
            </div>
          </div>

          <InvoiceLineEditor
            lines={lines}
            onChange={setLines}
            currency={currency}
            showTax={showTax}
          />

          <div className="space-y-1.5">
            <Label htmlFor="edit-notes">{t('invoices.field.notes')}</Label>
            <Input
              id="edit-notes"
              data-testid="edit-notes-input"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            data-testid="edit-submit"
          >
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
