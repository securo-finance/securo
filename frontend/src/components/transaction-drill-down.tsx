import { useEffect, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { useQuery } from '@tanstack/react-query'
import { transactions as transactionsApi, dashboard, admin } from '@/lib/api'
import { AlertTriangle, Clock, Info, Paperclip, X } from 'lucide-react'
import { CategoryIcon } from '@/components/category-icon'
import { TransactionCategoryIcon } from '@/components/category-lines-display'
import { isSplitByCategory } from '@/lib/category-lines'
import { ProjectedTransactionBadge } from '@/components/projected-transaction-badge'
import { sumDrillDownTotals } from '@/lib/drill-down-totals'
import { hasOpenDialog, isInsideOverlayLayer } from '@/lib/overlay-layers'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useAuth } from '@/contexts/auth-context'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import type { Transaction } from '@/types'
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
  /** Full amount of a split row when only one of its parts is listed. */
  partOf: number | null
}

export function TransactionDrillDown({
  filter,
  onClose,
  onTransactionClick,
  useViewerShare = false,
}: {
  filter: DrillDownFilter | null
  onClose: () => void
  onTransactionClick?: (tx: Transaction) => void
  useViewerShare?: boolean
}) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { mask } = usePrivacyMode()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const panelRef = useRef<HTMLDivElement>(null)
  const noAccounts = filter?.account_ids?.length === 0

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['drill-down', filter],
    queryFn: async () => {
      const params = {
        category_id: filter?.category_id,
        uncategorized: filter?.uncategorized,
        account_id: filter?.account_id,
        account_ids: filter?.account_ids,
        type: filter?.type,
        from: filter?.from,
        to: filter?.to,
        limit: 200,
        user_pnl_only: true,
      }
      const first = await transactionsApi.list({ ...params, page: 1 })
      const items = [...first.items]
      for (let page = 2; items.length < first.total; page++) {
        const next = await transactionsApi.list({ ...params, page })
        // Never silently present a truncated list and total as complete.
        if (next.items.length === 0) throw new Error('Incomplete transaction response')
        items.push(...next.items)
      }
      return { ...first, items }
    },
    enabled: !!filter && !noAccounts,
  })

  // Derive month param from filter.from for projected transactions
  const monthParam = filter?.from ? filter.from.slice(0, 7) + '-01' : undefined

  const { data: projectedTxs, isLoading: projectionsLoading, isError: projectionsError, refetch: refetchProjections } = useQuery({
    queryKey: ['dashboard', 'projected-transactions', monthParam],
    queryFn: () => dashboard.projectedTransactions({ month: monthParam }),
    enabled: !!filter && !!monthParam && !noAccounts,
  })

  const { data: accountingModeData } = useQuery({
    queryKey: ['admin', 'accounting-mode'],
    queryFn: () => admin.accountingMode(),
    staleTime: 5 * 60 * 1000,
  })
  const isAccrual = accountingModeData?.mode === 'accrual'
  const loading = !noAccounts && (isLoading || projectionsLoading)
  const loadError = !noAccounts && (isError || projectionsError)

  // Merge real + projected transactions, filtering projected by drill-down criteria
  const displayItems = useMemo((): DisplayItem[] => {
    const items: DisplayItem[] = []
    if (noAccounts) return items

    for (const tx of data?.items ?? []) {
      // Category splits and group splits are mutually exclusive in the API.
      // Keep the category-matched part and its converted amount authoritative;
      // otherwise apply the viewer's group share for unfiltered Budgets.
      const isPart = tx.matched_amount != null
      const matchedLine = isPart
        ? tx.category_lines?.find((line) => line.category_id === filter?.category_id)
        : undefined
      const parentAmount = Number(tx.amount)
      const viewerShare = useViewerShare && tx.viewer_share != null ? Number(tx.viewer_share) : null
      const amount = isPart ? Number(tx.matched_amount) : viewerShare ?? parentAmount
      const parentPrimary = tx.amount_primary != null ? Number(tx.amount_primary) : null
      const amountPrimary = isPart
        ? tx.matched_amount_primary != null ? Number(tx.matched_amount_primary) : null
        : viewerShare == null ? parentPrimary
          : viewerShare === 0 ? 0 : parentPrimary != null && parentAmount !== 0
            ? parentPrimary * Math.abs(viewerShare / parentAmount)
            : null
      items.push({
        key: tx.id,
        description: tx.description,
        date: tx.date,
        type: tx.type as 'debit' | 'credit',
        amount,
        amountPrimary,
        currency: tx.currency,
        categoryIcon: (matchedLine?.category ?? tx.category)?.icon ?? null,
        categoryName: (matchedLine?.category ?? tx.category)?.name ?? null,
        categoryColor: (matchedLine?.category ?? tx.category)?.color ?? null,
        isProjected: false,
        isPending: tx.status === 'pending',
        attachmentCount: tx.attachment_count ?? 0,
        transaction: tx,
        partOf: isPart ? Math.abs(Number(tx.amount)) : null,
      })
    }

    for (const pt of projectedTxs ?? []) {
      // Filter projected txs by drill-down criteria
      if (filter?.account_id && pt.account_id !== filter.account_id) continue
      if (filter?.account_ids && (!pt.account_id || !filter.account_ids.includes(pt.account_id))) continue
      if (filter?.type && pt.type !== filter.type) continue
      // A split template counts in a category through its part there.
      const parts = pt.has_category_lines ? pt.category_lines ?? [] : []
      // Every part ignored: the template counts nowhere, so it lists nowhere.
      if (pt.has_category_lines && !parts.length) continue
      const matchedParts = filter?.category_id
        ? parts.filter((part) => part.category_id === filter.category_id)
        : []
      if (filter?.category_id && !matchedParts.length && String(pt.category_id) !== filter.category_id) continue
      if (filter?.uncategorized && (pt.category_id != null || parts.length)) continue
      if (filter?.from && pt.date < filter.from) continue
      if (filter?.to && pt.date > filter.to) continue
      if (!filter?.category_id && parts.length) {
        // Income and expense views count only the parts that are income or
        // expense, as the projected totals do.
        const counted = parts.filter((part) => !part.treat_as_transfer)
        const share = counted.reduce((sum, part) => sum + part.amount, 0)
        if (!share) continue
        const ratio = pt.amount ? share / Math.abs(pt.amount) : 0
        items.push({
          key: `proj-${pt.recurring_id}-${pt.date}`,
          description: pt.description,
          date: pt.date,
          type: pt.type,
          amount: share,
          amountPrimary: pt.amount_primary != null ? pt.amount_primary * ratio : null,
          currency: pt.currency,
          categoryIcon: null,
          categoryName: null,
          categoryColor: null,
          isProjected: true,
          isPending: false,
          attachmentCount: 0,
          partOf: share !== Math.abs(pt.amount) ? Math.abs(pt.amount) : null,
          transaction: null,
        })
        continue
      }
      if (matchedParts.length) {
        const share = matchedParts.reduce((sum, part) => sum + part.amount, 0)
        const ratio = pt.amount ? share / Math.abs(pt.amount) : 0
        items.push({
          key: `proj-${pt.recurring_id}-${pt.date}`,
          description: pt.description,
          date: pt.date,
          type: pt.type,
          amount: share,
          amountPrimary: pt.amount_primary != null ? pt.amount_primary * ratio : null,
          currency: pt.currency,
          categoryIcon: matchedParts[0].category_icon,
          categoryName: matchedParts[0].category_name,
          categoryColor: matchedParts[0].category_color,
          isProjected: true,
          isPending: false,
          attachmentCount: 0,
          partOf: Math.abs(pt.amount),
          transaction: null,
        })
        continue
      }

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
        partOf: null,
        transaction: null,
      })
    }

    items.sort((a, b) => a.date.localeCompare(b.date))
    return items
  }, [data, projectedTxs, filter, noAccounts, useViewerShare])

  const isOpen = !!filter
  useEffect(() => {
    if (!isOpen) return
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    panelRef.current?.focus()
    return () => { opener?.focus() }
  }, [isOpen])

  // Close on Escape, unless the key belongs to a dialog opened from the
  // panel (Radix marks the Escape it handled as defaultPrevented).
  useEffect(() => {
    if (!filter) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || hasOpenDialog()) return
      onClose()
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [filter, onClose])

  // Close on click outside. The transaction dialog opened from a row, and its
  // popovers and selects, are portaled to the body; clicks there keep the
  // panel open so the next row can be edited once the dialog closes.
  useEffect(() => {
    if (!filter) return
    const handleClick = (e: MouseEvent) => {
      if (!panelRef.current || panelRef.current.contains(e.target as Node)) return
      if (isInsideOverlayLayer(e.target) || hasOpenDialog()) return
      onClose()
    }
    // Delay to avoid closing immediately from the click that opened it
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClick)
    }, 100)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('mousedown', handleClick)
    }
  }, [filter, onClose])

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
        role="complementary"
        aria-label={filter?.title}
        tabIndex={-1}
        inert={!filter}
        aria-hidden={!filter}
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
            onClick={onClose}
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
          {loadError ? (
            <div role="alert" className="p-5 text-center space-y-3">
              <p className="text-sm text-destructive">{t('dashboard.drillDownError')}</p>
              <button type="button" className="text-sm text-primary hover:underline" onClick={() => {
                void refetch()
                if (monthParam && !noAccounts) void refetchProjections()
              }}>{t('common.retry')}</button>
            </div>
          ) : loading ? (
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
                  className={`flex items-center gap-3 px-5 py-3 hover:bg-muted transition-colors ${!item.isProjected && onTransactionClick ? 'cursor-pointer' : ''}`}
                  onClick={() => {
                    if (!item.isProjected && item.transaction) {
                      onTransactionClick?.(item.transaction)
                    }
                  }}
                >
                  {item.transaction && isSplitByCategory(item.transaction) && item.partOf == null ? (
                    <TransactionCategoryIcon tx={item.transaction} size="lg" />
                  ) : (
                    <CategoryIcon
                      icon={item.categoryIcon}
                      color={item.categoryColor}
                      size="lg"
                    />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-foreground truncate">{item.description}</p>
                      {item.isProjected && (
                        <ProjectedTransactionBadge />
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
                      {item.partOf != null && ` · ${t('categoryLines.partOf', {
                        value: mask(formatCurrency(item.partOf, item.currency ?? userCurrency, locale)),
                      })}`}
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
        {!loadError && !loading && displayItems.length > 0 && (
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
