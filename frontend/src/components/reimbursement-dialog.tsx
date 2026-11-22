import { useDeferredValue, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertTriangle, Search, Undo2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { transactions as transactionsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { getAccountName } from '@/lib/account-utils'
import { formatAmountInput, formatCurrency, ltrAmount, parseAmountInput } from '@/lib/format'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { formatSignedNet } from '@/lib/reimbursements'
import { useDateLocale, useDisplayLocale } from '@/hooks/use-display-locale'
import type { ReimbursementLinkedTransaction, Transaction } from '@/types'

/** What the dialog needs of an account: its id and how to name it. */
export type ReimbursementAccount = { id: string; name: string; display_name?: string | null }

/**
 * `counterparts`: link the rows that pay `transaction` back (it is the
 * anchor). `anchors`: link `transaction` as paying back other rows (it is
 * the counterpart).
 */
export type ReimbursementDialogMode = 'counterparts' | 'anchors'

type Props = {
  open: boolean
  onClose: () => void
  transaction: Transaction | null
  mode: ReimbursementDialogMode
  accounts: ReimbursementAccount[]
}

type Draft = {
  row: ReimbursementLinkedTransaction
  amount: string
  // How much of the row this anchor may take: what is left of it plus what
  // this anchor already takes.
  available: number
}

const CENT = 0.005

function signed(row: { type: string; amount: number | string; currency: string }, locale: string): string {
  const sign = row.type === 'credit' ? '+' : '−'
  return ltrAmount(`${sign}${formatCurrency(Math.abs(Number(row.amount)), row.currency, locale)}`)
}

function RowSummary({
  row,
  accounts,
  dateLocale,
  extra,
}: {
  row: ReimbursementLinkedTransaction | Transaction
  accounts: ReimbursementAccount[]
  dateLocale: string
  extra?: string
}) {
  const account = accounts.find((a) => a.id === row.account_id)
  return (
    <div className="min-w-0 flex-1">
      <p className="text-sm font-semibold text-foreground truncate" title={row.description}>
        {row.description}
      </p>
      <p className="text-xs text-muted-foreground truncate">
        {account ? getAccountName(account) : '—'} · {new Date(row.date + 'T00:00:00').toLocaleDateString(dateLocale)}
        {extra ? ` · ${extra}` : ''}
      </p>
    </div>
  )
}

export function ReimbursementDialog({ open, onClose, transaction, mode, accounts }: Props) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const queryClient = useQueryClient()

  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search.trim())
  const [draft, setDraft] = useState<Draft[] | null>(null)
  const [picked, setPicked] = useState<{ row: ReimbursementLinkedTransaction; amount: string } | null>(null)

  // Start over whenever the dialog opens on another row.
  const sessionKey = `${open ? '1' : '0'}-${transaction?.id ?? ''}-${mode}`
  const [prevSessionKey, setPrevSessionKey] = useState(sessionKey)
  if (sessionKey !== prevSessionKey) {
    setPrevSessionKey(sessionKey)
    setSearch('')
    setDraft(null)
    setPicked(null)
  }

  const txId = transaction?.id
  const { data: links, isLoading: linksLoading } = useQuery({
    queryKey: ['reimbursements', txId],
    queryFn: () => transactionsApi.reimbursements(txId!),
    enabled: open && !!txId,
  })
  const { data: candidates, isLoading: candidatesLoading } = useQuery({
    queryKey: ['reimbursement-candidates', txId, mode, deferredSearch],
    queryFn: () =>
      transactionsApi.reimbursementCandidates(txId!, {
        looking_for: mode,
        q: deferredSearch || undefined,
      }),
    enabled: open && !!txId,
  })

  const onSaved = () => {
    invalidateFinancialQueries(queryClient)
    queryClient.invalidateQueries({ queryKey: ['reimbursements'] })
    queryClient.invalidateQueries({ queryKey: ['reimbursement-candidates'] })
    toast.success(t('reimbursements.saved'))
  }
  const onError = (error: unknown) => toast.error(extractApiError(error, t('common.error')))

  const saveCounterparts = useMutation({
    mutationFn: (allocations: { counterpart_id: string; amount: string }[]) =>
      transactionsApi.replaceReimbursements(txId!, allocations),
    onSuccess: () => {
      onSaved()
      onClose()
    },
    onError,
  })

  // From the counterpart's side, a link lives on the anchor: read the
  // anchor's allocations, change this row's, and write them all back.
  const saveOnAnchor = useMutation({
    mutationFn: async ({ anchorId, amount }: { anchorId: string; amount: string | null }) => {
      const current = await transactionsApi.reimbursements(anchorId)
      const others = current.counterparts
        .filter((c) => c.counterpart_id !== txId)
        .map((c) => ({ counterpart_id: c.counterpart_id, amount: String(c.amount) }))
      const allocations = amount == null ? others : [...others, { counterpart_id: txId!, amount }]
      return transactionsApi.replaceReimbursements(anchorId, allocations)
    },
    onSuccess: () => {
      onSaved()
      setPicked(null)
    },
    onError,
  })

  if (!open || !transaction) return null

  const focusAmount = Math.abs(Number(transaction.amount))
  const busy = saveCounterparts.isPending || saveOnAnchor.isPending
  const toDecimal = (value: number) => value.toFixed(2)

  const savedDraft: Draft[] = (links?.counterparts ?? []).map((c) => ({
    row: c.transaction,
    amount: formatAmountInput(Number(c.amount), locale, 2, 2),
    available: Number(c.transaction.unallocated) + Number(c.amount),
  }))
  const rows = draft ?? savedDraft
  const parsed = rows.map((d) => parseAmountInput(d.amount, locale))
  const invalid = rows.some((d, i) => {
    const value = parsed[i]
    return value == null || value <= 0 || value > d.available + CENT
  })
  const sameCurrency = rows.every((d) => d.row.currency === transaction.currency)
  const paidBack = parsed.reduce<number>((sum, value) => sum + (value ?? 0), 0)
  const net = focusAmount - paidBack
  const netLabel = sameCurrency
    ? ltrAmount(formatSignedNet(net, transaction.type, transaction.currency, locale))
    : null

  const linkedIds = new Set(
    mode === 'counterparts' ? rows.map((d) => d.row.id) : (links?.anchors ?? []).map((a) => a.anchor_id),
  )
  const shownCandidates = (candidates ?? []).filter((c) => !linkedIds.has(c.id))

  const addCounterpart = (row: ReimbursementLinkedTransaction) => {
    const available = Number(row.unallocated)
    const remaining = row.currency === transaction.currency ? focusAmount - paidBack : available
    const amount = remaining > CENT ? Math.min(available, remaining) : available
    setDraft([...rows, { row, amount: formatAmountInput(amount, locale, 2, 2), available }])
  }

  const focusUnallocated = Number(links?.unallocated ?? focusAmount)
  const pickAnchor = (row: ReimbursementLinkedTransaction) => {
    const amount = Math.min(focusUnallocated, Math.abs(Number(row.amount)))
    setPicked({ row, amount: formatAmountInput(Math.max(amount, 0), locale, 2, 2) })
  }
  const pickedValue = picked ? parseAmountInput(picked.amount, locale) : null
  const pickedInvalid = pickedValue == null || pickedValue <= 0 || pickedValue > focusUnallocated + CENT

  const title = mode === 'counterparts' ? t('reimbursements.title') : t('reimbursements.linkAsTitle')
  const description =
    mode === 'counterparts' ? t('reimbursements.counterpartsDescription') : t('reimbursements.anchorsDescription')

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden p-4 sm:max-w-xl sm:p-6">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <div className="min-h-0 min-w-0 space-y-4 overflow-y-auto overscroll-contain pe-1">
          <DialogDescription>{description}</DialogDescription>

          <div className="flex items-center gap-3 rounded-lg border border-border bg-muted/30 p-3">
            <RowSummary row={transaction} accounts={accounts} dateLocale={dateLocale} />
            <p className={`text-sm font-bold tabular-nums shrink-0 ${transaction.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
              {signed(transaction, locale)}
            </p>
          </div>

          {links?.over_allocated && (
            <div className="flex items-start gap-2 p-3 text-xs bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 rounded-md text-amber-800 dark:text-amber-300">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <span>{t('reimbursements.overAllocated')}</span>
            </div>
          )}

          <section>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
              {mode === 'counterparts' ? t('reimbursements.linked') : t('reimbursements.paysBack')}
            </p>
            {linksLoading ? (
              <Skeleton className="h-14 w-full" />
            ) : mode === 'counterparts' ? (
              rows.length === 0 ? (
                <p className="text-xs text-muted-foreground italic py-2">{t('reimbursements.noLinks')}</p>
              ) : (
                <ul className="space-y-1.5">
                  {rows.map((d, i) => {
                    const value = parsed[i]
                    const tooMuch = value != null && value > d.available + CENT
                    return (
                      <li key={d.row.id} className="flex items-center gap-2 rounded-lg border border-border bg-card p-2">
                        <RowSummary
                          row={d.row}
                          accounts={accounts}
                          dateLocale={dateLocale}
                          extra={t('reimbursements.available', {
                            amount: ltrAmount(formatCurrency(d.available, d.row.currency, locale)),
                          })}
                        />
                        <Input
                          type="text"
                          inputMode="decimal"
                          aria-label={t('reimbursements.amountFor', { description: d.row.description })}
                          aria-invalid={tooMuch || undefined}
                          className="w-28 bg-card text-end tabular-nums"
                          value={d.amount}
                          onChange={(e) =>
                            setDraft(rows.map((r) => (r.row.id === d.row.id ? { ...r, amount: e.target.value } : r)))
                          }
                        />
                        <span className="text-xs text-muted-foreground shrink-0">{d.row.currency}</span>
                        <button
                          type="button"
                          aria-label={t('reimbursements.remove', { description: d.row.description })}
                          onClick={() => setDraft(rows.filter((r) => r.row.id !== d.row.id))}
                          className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted"
                        >
                          <X size={14} />
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )
            ) : (links?.anchors ?? []).length === 0 ? (
              <p className="text-xs text-muted-foreground italic py-2">{t('reimbursements.noLinks')}</p>
            ) : (
              <ul className="space-y-1.5">
                {(links?.anchors ?? []).map((a) => (
                  <li key={a.id} className="flex items-center gap-2 rounded-lg border border-border bg-card p-2">
                    <RowSummary row={a.transaction} accounts={accounts} dateLocale={dateLocale} />
                    <span className="text-sm tabular-nums shrink-0">
                      {ltrAmount(formatCurrency(Number(a.amount), transaction.currency, locale))}
                    </span>
                    <button
                      type="button"
                      disabled={busy}
                      aria-label={t('reimbursements.remove', { description: a.transaction.description })}
                      onClick={() => saveOnAnchor.mutate({ anchorId: a.anchor_id, amount: null })}
                      className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-50"
                    >
                      <X size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {mode === 'counterparts' && rows.length > 0 && netLabel && (
              <p className="mt-2 text-xs text-muted-foreground tabular-nums">
                {t('reimbursements.netPreview', { amount: netLabel })}
              </p>
            )}
            {mode === 'anchors' && (
              <p className="mt-2 text-xs text-muted-foreground tabular-nums">
                {t('reimbursements.available', {
                  amount: ltrAmount(formatCurrency(focusUnallocated, transaction.currency, locale)),
                })}
              </p>
            )}
          </section>

          {picked ? (
            <section className="space-y-2 rounded-lg border border-primary/30 p-3">
              <div className="flex items-center gap-2">
                <RowSummary row={picked.row} accounts={accounts} dateLocale={dateLocale} />
                <span className="text-sm tabular-nums shrink-0">{signed(picked.row, locale)}</span>
              </div>
              <div className="flex items-center gap-2">
                <Input
                  type="text"
                  inputMode="decimal"
                  aria-label={t('reimbursements.amountFor', { description: picked.row.description })}
                  aria-invalid={(pickedValue != null && pickedInvalid) || undefined}
                  className="w-32 bg-card text-end tabular-nums"
                  value={picked.amount}
                  onChange={(e) => setPicked({ ...picked, amount: e.target.value })}
                />
                <span className="text-xs text-muted-foreground">{transaction.currency}</span>
                <Button type="button" variant="ghost" className="ms-auto" onClick={() => setPicked(null)} disabled={busy}>
                  {t('common.cancel')}
                </Button>
                <Button
                  type="button"
                  disabled={pickedInvalid || busy}
                  onClick={() =>
                    saveOnAnchor.mutate({ anchorId: picked.row.id, amount: toDecimal(pickedValue ?? 0) })
                  }
                >
                  {t('reimbursements.link')}
                </Button>
              </div>
            </section>
          ) : (
            <section>
              <div className="relative mb-2">
                <Search size={14} className="absolute start-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t('reimbursements.searchPlaceholder')}
                  aria-label={t('reimbursements.searchPlaceholder')}
                  className="w-full ps-9 pe-3 py-2 text-sm rounded-md border border-border bg-card text-foreground focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                />
              </div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                {deferredSearch ? t('reimbursements.results') : t('reimbursements.suggestions')}
              </p>
              {candidatesLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-14 w-full" />
                  ))}
                </div>
              ) : shownCandidates.length === 0 ? (
                <p className="text-xs text-muted-foreground italic py-4 text-center">
                  {t('reimbursements.noCandidates')}
                </p>
              ) : (
                <ul className="max-h-72 overflow-y-auto space-y-1.5 -mx-1 px-1">
                  {shownCandidates.map((c) => (
                    <li key={c.id}>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => (mode === 'counterparts' ? addCounterpart(c) : pickAnchor(c))}
                        className="w-full text-start rounded-lg border border-border bg-card hover:bg-muted/50 hover:border-primary/40 transition-colors p-3 flex items-center gap-3 min-w-0"
                      >
                        <RowSummary
                          row={c}
                          accounts={accounts}
                          dateLocale={dateLocale}
                          extra={
                            mode === 'counterparts' && Number(c.unallocated) !== Math.abs(Number(c.amount))
                              ? t('reimbursements.available', {
                                  amount: ltrAmount(formatCurrency(Number(c.unallocated), c.currency, locale)),
                                })
                              : undefined
                          }
                        />
                        <span
                          className={`text-sm font-bold tabular-nums shrink-0 ${c.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}
                        >
                          {signed(c, locale)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </div>

        <DialogFooter className="shrink-0">
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            {mode === 'counterparts' ? t('common.cancel') : t('common.close')}
          </Button>
          {mode === 'counterparts' && (
            <Button
              type="button"
              disabled={invalid || busy || draft == null}
              onClick={() =>
                saveCounterparts.mutate(
                  rows.map((d, i) => ({ counterpart_id: d.row.id, amount: toDecimal(parsed[i] ?? 0) })),
                )
              }
            >
              <Undo2 size={14} className="me-1" />
              {busy ? t('common.loading') : t('common.save')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
