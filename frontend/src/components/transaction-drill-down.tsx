import { useCallback, useEffect, useRef, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { transactions as transactionsApi, dashboard, admin } from '@/lib/api'
import { AlertTriangle, CircleDot, Clock, Info, Paperclip, Star, X } from 'lucide-react'
import { CategoryIcon } from '@/components/category-icon'
import { ProjectedTransactionBadge } from '@/components/projected-transaction-badge'
import { ReviewAttributionBadge } from '@/components/review-attribution-badge'
import { sumDrillDownTotals } from '@/lib/drill-down-totals'
import { hasOpenDialog, isInsideOverlayLayer } from '@/lib/overlay-layers'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { toast } from 'sonner'
import { extractApiError } from '@/lib/api-errors'
import { useWorkspace } from '@/contexts/workspace-context'
import { useAuth } from '@/contexts/auth-context'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import type { PaginatedTransactions, Transaction } from '@/types'
import { formatCurrency } from '@/lib/format'

export type DrillDownFilter = {
  title: string
  category_id?: string
  uncategorized?: boolean
  account_id?: string
  // Scope to a set of accounts (e.g. the active collection's accounts).
  account_ids?: string[]
  type?: 'credit' | 'debit'
  from?: string
  to?: string
}

type DisplayItem = {
  key: string
  description: string
  date: string
  type: 'debit' | 'credit'
  amount: number
  amountPrimary: number | null
  currency: string
  categoryIcon: string | null
  categoryName: string | null
  categoryColor: string | null
  isProjected: boolean
  isPending: boolean
  attachmentCount: number
  transaction: Transaction | null
}

export function TransactionDrillDown({
  filter,
  onClose,
  onTransactionClick,
}: {
  filter: DrillDownFilter | null
  onClose: () => void
  onTransactionClick?: (tx: Transaction) => void
}) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const reviewInFlight = useRef(false)
  const { mask } = usePrivacyMode()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const panelRef = useRef<HTMLDivElement>(null)
  // PR-2 §5.4: the row under the pointer is the shortcut target. No
  // active row (or a projected row, which has no Transaction) = no-op.
  const [activeRowKey, setActiveRowKey] = useState<string | null>(null)
  // Equal filter objects keep intent; a semantic replacement (including
  // close/reopen) must require a new mouse entry even if the same ID returns.
  const filterIdentity = JSON.stringify(filter ? [
    filter.title, filter.category_id, filter.uncategorized, filter.account_id,
    filter.account_ids ? [...filter.account_ids].sort() : null, filter.type, filter.from, filter.to,
  ] : null)
  const [intentFilter, setIntentFilter] = useState(filterIdentity)
  if (intentFilter !== filterIdentity) {
    setIntentFilter(filterIdentity)
    setActiveRowKey(null)
  }
  const queryClient = useQueryClient()
  const close = useCallback(() => {
    setActiveRowKey(null)
    onClose()
  }, [onClose])

  // Shift+R toggles review on the active row — one key, both directions.
  // Same dialog guard as the Escape layer so typing in the edit dialog
  // never fires it.
  const toggleReviewMutation = useMutation({
    mutationFn: (tx: Transaction) =>
      transactionsApi.setReview(
        tx.id,
        tx.review_status === 'reviewed' ? 'needs_review' : 'reviewed',
      ),
    onSuccess: (updated) => {
      // Patch the panel's own cache so the indicator flips immediately,
      // then let the list page refetch under its own key.
      queryClient.setQueryData<PaginatedTransactions>(
        ['drill-down', filter],
        (old) =>
          old
            ? { ...old, items: old.items.map((i) => (i.id === updated.id ? updated : i)) }
            : old,
      )
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
    },
    onError: (error) => toast.error(extractApiError(error)),
    onSettled: () => { reviewInFlight.current = false },
  })

  const { data, isLoading } = useQuery({
    queryKey: ['drill-down', filter],
    queryFn: () =>
      transactionsApi.list({
        category_id: filter?.category_id,
        uncategorized: filter?.uncategorized,
        account_id: filter?.account_id,
        account_ids: filter?.account_ids,
        type: filter?.type,
        from: filter?.from,
        to: filter?.to,
        limit: 200,
        user_pnl_only: true,
      }),
    enabled: !!filter,
  })

  // Derive month param from filter.from for projected transactions
  const monthParam = filter?.from ? filter.from.slice(0, 7) + '-01' : undefined

  const { data: projectedTxs } = useQuery({
    queryKey: ['dashboard', 'projected-transactions', monthParam],
    queryFn: () => dashboard.projectedTransactions({ month: monthParam }),
    enabled: !!filter && !!monthParam,
  })

  const { data: accountingModeData } = useQuery({
    queryKey: ['admin', 'accounting-mode'],
    queryFn: () => admin.accountingMode(),
    staleTime: 5 * 60 * 1000,
  })
  const isAccrual = accountingModeData?.mode === 'accrual'

  // Merge real + projected transactions, filtering projected by drill-down criteria
  const displayItems = useMemo((): DisplayItem[] => {
    const items: DisplayItem[] = []

    for (const tx of data?.items ?? []) {
      items.push({
        key: tx.id,
        description: tx.description,
        date: tx.date,
        type: tx.type as 'debit' | 'credit',
        amount: Number(tx.amount),
        amountPrimary: tx.amount_primary != null ? Number(tx.amount_primary) : null,
        currency: tx.currency,
        categoryIcon: tx.category?.icon ?? null,
        categoryName: tx.category?.name ?? null,
        categoryColor: tx.category?.color ?? null,
        isProjected: false,
        isPending: tx.status === 'pending',
        attachmentCount: tx.attachment_count ?? 0,
        transaction: tx,
      })
    }

    for (const pt of projectedTxs ?? []) {
      // Filter projected txs by drill-down criteria
      if (filter?.type && pt.type !== filter.type) continue
      if (filter?.category_id && String(pt.category_id) !== filter.category_id) continue
      if (filter?.uncategorized && pt.category_id != null) continue
      if (filter?.from && pt.date < filter.from) continue
      if (filter?.to && pt.date > filter.to) continue

      items.push({
        key: `proj-${pt.recurring_id}-${pt.date}`,
        description: pt.description,
        date: pt.date,
        type: pt.type,
        amount: pt.amount,
        amountPrimary: pt.amount_primary ?? null,
        currency: pt.currency,
        categoryIcon: pt.category_icon,
        categoryName: pt.category_name,
        categoryColor: pt.category_color ?? null,
        isProjected: true,
        isPending: false,
        attachmentCount: 0,
        transaction: null,
      })
    }

    items.sort((a, b) => a.date.localeCompare(b.date))
    return items
  }, [data, projectedTxs, filter])

  // Close on Escape, unless the key belongs to a dialog opened from the
  // panel (Radix marks the Escape it handled as defaultPrevented).
  useEffect(() => {
    if (!filter) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || hasOpenDialog()) return
      close()
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [filter, close])

  // PR-2 §5.4: Shift+R toggles review on the active row.
  useEffect(() => {
    if (!filter) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== 'r' || !e.shiftKey) return
      if (e.defaultPrevented || e.repeat || e.ctrlKey || e.altKey || e.metaKey || hasOpenDialog()) return
      if (!canWrite || reviewInFlight.current || toggleReviewMutation.isPending) return
      const target = e.target instanceof Element ? e.target : null
      const focused = document.activeElement
      const editable = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"]'
      if (target?.closest(editable) || focused?.closest(editable)) return
      const item = displayItems.find((i) => i.key === activeRowKey)
      if (!item?.transaction || item.transaction.is_shared || item.transaction.virtual) return
      e.preventDefault()
      reviewInFlight.current = true
      toggleReviewMutation.mutate(item.transaction)
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [filter, activeRowKey, displayItems, toggleReviewMutation, canWrite])

  // Close on click outside. The transaction dialog opened from a row, and its
  // popovers and selects, are portaled to the body; clicks there keep the
  // panel open so the next row can be edited once the dialog closes.
  useEffect(() => {
    if (!filter) return
    const handleClick = (e: MouseEvent) => {
      if (!panelRef.current || panelRef.current.contains(e.target as Node)) return
      if (isInsideOverlayLayer(e.target) || hasOpenDialog()) return
      close()
    }
    // Delay to avoid closing immediately from the click that opened it
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClick)
    }, 100)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('mousedown', handleClick)
    }
  }, [filter, close])

  // Sum in user's primary currency. For foreign-currency rows we need
  // amount_primary; if it's missing we can't convert, so skip the row
  // instead of adding a raw foreign amount as if it were primary. This
  // matches how get_summary computes monthly_*_primary on the backend.
  const { absTotal, postedTotal, pendingTotal, projectedTotal } =
    sumDrillDownTotals(displayItems, userCurrency)

  // Break the total down whenever some of it is money that has not settled,
  // whether it is pending or still only projected. Gating on pending alone
  // hid the projected line from a panel that happened to have no pending row,
  // even though the total it sits under already counted the projection.
  const hasUnsettledTotal = pendingTotal > 0 || projectedTotal > 0

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 bg-black/20 z-40 transition-opacity duration-200 ${
          filter ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        className={`fixed top-0 right-0 h-full w-full max-w-md bg-card shadow-2xl z-50 transform transition-transform duration-200 ease-out flex flex-col ${
          filter ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <h2 className="text-sm font-semibold text-foreground truncate pr-4">
            {filter?.title}
          </h2>
          <button
            onClick={close}
            className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors shrink-0"
            title={t('common.close')}
          >
            <X size={16} />
          </button>
        </div>

        {isAccrual && filter?.from && (
          <div className="flex items-start gap-2 px-5 py-2.5 bg-muted/40 border-b border-border text-[11px] text-muted-foreground shrink-0">
            <Info size={12} className="mt-0.5 shrink-0" />
            <span>{t('dashboard.accrualNote')}</span>
          </div>
        )}

        {/* Transaction list */}
        <div className="flex-1 overflow-auto">
          {isLoading ? (
            <div className="p-5 space-y-3">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="h-12 bg-muted rounded-lg animate-pulse" />
              ))}
            </div>
          ) : displayItems.length === 0 ? (
            <p className="text-muted-foreground text-sm text-center py-12">
              {t('dashboard.drillDownEmpty')}
            </p>
          ) : (
            <div className="divide-y divide-border">
              {displayItems.map((item) => (
                <div
                  key={item.key}
                  className={`flex items-center gap-3 px-5 py-3 hover:bg-muted transition-colors ${!item.isProjected ? 'cursor-pointer' : ''}`}
                  title={
                    canWrite && item.transaction && !item.transaction.is_shared && !item.transaction.virtual
                      && !toggleReviewMutation.isPending ? t('transactions.reviewShortcutHint') : undefined
                  }
                  onMouseEnter={() => setActiveRowKey(item.key)}
                  onMouseLeave={() => setActiveRowKey((key) => key === item.key ? null : key)}
                  onClick={() => {
                    if (!item.isProjected && item.transaction) {
                      onTransactionClick?.(item.transaction)
                    }
                  }}
                >
                  <CategoryIcon
                    icon={item.categoryIcon}
                    color={item.categoryColor}
                    size="lg"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-foreground truncate">{item.description}</p>
                      {item.isProjected && (
                        <ProjectedTransactionBadge />
                      )}
                      {/* PR-2 indicators — same axis as the list page:
                          review state first, then the flag bookmark.
                          Native `title`, not Tooltip: the panel rows are
                          rendered inside tests without a TooltipProvider
                          and the pill on the list page uses `title` too. */}
                      {item.transaction?.review_status === 'needs_review' && (
                        <span
                          role="img"
                          aria-label={t('transactions.needsReview')}
                          title={t('transactions.needsReview')}
                          className="shrink-0 inline-flex items-center justify-center"
                        >
                          <CircleDot size={12} className="text-primary" />
                        </span>
                      )}
                      {item.transaction && (
                        <ReviewAttributionBadge transaction={item.transaction} />
                      )}
                      {item.transaction?.flagged && (
                        <Star size={12} className="shrink-0 fill-current text-amber-500" aria-label={t('transactions.flagged')} />
                      )}
                      {item.isPending && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="shrink-0 inline-flex items-center justify-center rounded-full border border-amber-200 bg-amber-50 p-0.5 dark:border-amber-500/30 dark:bg-amber-500/10">
                              <Clock size={12} className="text-amber-500" role="img" aria-label={t('transactions.pending')} />
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>{t('transactions.pending')}</TooltipContent>
                        </Tooltip>
                      )}
                      {item.attachmentCount > 0 && (
                        <Paperclip size={12} className="text-muted-foreground shrink-0" />
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {new Date(item.date + 'T00:00:00').toLocaleDateString(dateLocale)}
                      {item.categoryName && ` · ${item.categoryName}`}
                    </p>
                  </div>
                  <div className="text-right shrink-0">
                    <span
                      className={`text-sm font-semibold tabular-nums ${
                        item.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'
                      }`}
                    >
                      {item.type === 'credit' ? '+' : '-'}
                      {mask(formatCurrency(Math.abs(item.amount), item.currency ?? userCurrency, locale))}
                    </span>
                    {item.currency !== userCurrency && item.amountPrimary != null && (
                      <div className="flex items-center justify-end gap-1">
                        {item.transaction?.fx_fallback && (
                          <span title={t('transactions.fxFallbackTooltip')}><AlertTriangle size={11} className="text-amber-500 shrink-0" /></span>
                        )}
                        <span className="text-[10px] text-muted-foreground tabular-nums">
                          {mask(formatCurrency(Math.abs(item.amountPrimary), userCurrency, locale))}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        {displayItems.length > 0 && (
          <div className="px-5 py-3 border-t border-border bg-muted/50 shrink-0">
            {hasUnsettledTotal ? (
              <div className="space-y-1">
                <div className="flex items-center justify-between gap-4 text-xs text-muted-foreground">
                  <span>{t('dashboard.drillDownSettledTotal')}</span>
                  <span className="tabular-nums text-foreground">{mask(formatCurrency(postedTotal, userCurrency, locale))}</span>
                </div>
                <div className="flex items-center justify-between gap-4 text-xs text-muted-foreground">
                  <span>{t('dashboard.drillDownPendingTotal')}</span>
                  <span className="tabular-nums text-foreground">{mask(formatCurrency(pendingTotal, userCurrency, locale))}</span>
                </div>
                {projectedTotal > 0 && (
                  <div className="flex items-center justify-between gap-4 text-xs text-muted-foreground">
                    <span>{t('transactions.projected')}</span>
                    <span className="tabular-nums text-foreground">{mask(formatCurrency(projectedTotal, userCurrency, locale))}</span>
                  </div>
                )}
                <div className="flex items-center justify-between gap-4 border-t border-border pt-2 mt-2">
                  <span className="text-xs font-medium text-muted-foreground">{t('dashboard.drillDownShownTotal')}</span>
                  <span className="text-sm font-bold tabular-nums text-foreground">{mask(formatCurrency(absTotal, userCurrency, locale))}</span>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  {t('dashboard.drillDownTotal', {
                    count: displayItems.length,
                    total: mask(formatCurrency(absTotal, userCurrency, locale)),
                  })}
                </span>
                <span className="text-sm font-bold tabular-nums text-foreground">
                  {mask(formatCurrency(absTotal, userCurrency, locale))}
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  )
}
