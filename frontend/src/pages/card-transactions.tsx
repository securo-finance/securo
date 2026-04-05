import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, Check, Paperclip, Search, X } from 'lucide-react'
import { toast } from 'sonner'
import { accounts as accountsApi, categories as categoriesApi, months, recurring, transactions } from '@/lib/api'
import type { Transaction } from '@/types'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { CategoryIcon } from '@/components/category-icon'
import { TransactionDialog, extractApiError } from '@/components/transaction-dialog'
import { useCurrentMonth } from '@/hooks/use-current-month'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

function parseHashtags(notes: string | null): string[] {
  if (!notes) return []
  const matches = notes.match(/#[\w\u00C0-\u017E-]+/g)
  return matches ?? []
}

export default function CardTransactionsPage() {
  const { id } = useParams<{ id: string }>()
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const { data: currentMonthState } = useCurrentMonth()
  const currentMonthDefined = currentMonthState?.is_defined ?? false
  const isSnapshotView = currentMonthState?.is_snapshot_view ?? false
  const editableMonth = currentMonthDefined && !isSnapshotView
  const snapshots = currentMonthState?.snapshots ?? []
  const currentPeriodLabel = currentMonthState?.current_period_label ?? currentMonthState?.current_period ?? ''
  const selectedSnapshotPeriod = currentMonthState?.selected_mode === 'snapshot' ? currentMonthState.selected_period : ''
  const selectedPeriodLabel = currentMonthState?.selected_period_label ?? currentPeriodLabel
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const queryClient = useQueryClient()

  const [page, setPage] = useState(1)
  const [filterCategory, setFilterCategory] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingTx, setEditingTx] = useState<Transaction | null>(null)
  const [tagFilter, setTagFilter] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bulkCategory, setBulkCategory] = useState('')
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null)

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearchQuery(searchInput)
      setPage(1)
    }, 300)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [searchInput])

  useEffect(() => {
    setSelectedIds(new Set())
    setBulkCategory('')
  }, [page, filterCategory, searchQuery, selectedSnapshotPeriod])

  const { data: card, isLoading: cardLoading } = useQuery({
    queryKey: ['accounts', id],
    queryFn: () => accountsApi.get(id!),
    enabled: Boolean(id),
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const { data: recurringList } = useQuery({
    queryKey: ['recurring'],
    queryFn: recurring.list,
  })

  const { data, isLoading } = useQuery({
    queryKey: ['card-transactions', id, page, filterCategory, searchQuery, selectedSnapshotPeriod],
    queryFn: () =>
      transactions.list({
        page,
        limit: 20,
        account_id: id,
        category_id: filterCategory === '__uncategorized__' ? undefined : (filterCategory || undefined),
        uncategorized: filterCategory === '__uncategorized__' ? true : undefined,
        q: searchQuery || undefined,
      }),
    enabled: Boolean(id),
  })

  const setMonthViewMutation = useMutation({
    mutationFn: ({ mode, period }: { mode: 'current' | 'snapshot'; period?: string }) =>
      months.setView(mode, period),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['current-month'] })
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['card-transactions'] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      toast.success(
        data.selected_mode === 'snapshot'
          ? t('dashboard.snapshotViewActivated', { period: data.selected_period_label ?? data.selected_period })
          : data.current_period
            ? t('dashboard.returnedToCurrentMonth', { period: data.current_period_label ?? data.current_period })
            : t('dashboard.returnedToMonthSetupToast')
      )
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id: transactionId, ...payload }: Partial<Transaction> & { id: string }) =>
      transactions.update(transactionId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['card-transactions'] })
      setDialogOpen(false)
      setEditingTx(null)
      toast.success(t('transactions.updated'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (transactionId: string) => transactions.delete(transactionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['card-transactions'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      setDialogOpen(false)
      setEditingTx(null)
      toast.success(t('transactions.deleted'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const bulkCategorizeMutation = useMutation({
    mutationFn: ({ ids, categoryId }: { ids: string[]; categoryId: string | null }) =>
      transactions.bulkCategorize(ids, categoryId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['card-transactions'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      setSelectedIds(new Set())
      setBulkCategory('')
      toast.success(t('transactions.bulkSuccess', { count: result.updated }))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const filteredItems = useMemo(() => {
    if (!tagFilter || !data?.items) return data?.items ?? []
    return data.items.filter((tx) => tx.notes?.includes(tagFilter))
  }, [data?.items, tagFilter])

  const toggleSelect = (transactionId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(transactionId)) next.delete(transactionId)
      else next.add(transactionId)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (!filteredItems.length) return
    const allSelected = filteredItems.every((tx) => selectedIds.has(tx.id))
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(filteredItems.map((tx) => tx.id)))
    }
  }

  const allSelected = filteredItems.length > 0 && filteredItems.every((tx) => selectedIds.has(tx.id))
  const someSelected = filteredItems.some((tx) => selectedIds.has(tx.id)) && !allSelected
  const totalPages = data ? Math.ceil(data.total / 20) : 0

  if (!id) {
    return <Navigate to="/cards" replace />
  }

  if (!cardLoading && (!card || card.type !== 'credit_card')) {
    return (
      <div>
        <PageHeader section={t('cards.detailSection')} title={t('cards.unknownCard')} />
        <div className="rounded-xl border border-dashed border-border bg-card px-6 py-12 text-center shadow-sm">
          <h2 className="text-lg font-semibold text-foreground">{t('cards.notFound')}</h2>
          <p className="mt-2 text-sm text-muted-foreground">{t('cards.notFoundHint')}</p>
          <Button asChild variant="outline" className="mt-4">
            <Link to="/cards">{t('cards.backToCards')}</Link>
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        section={t('cards.detailSection')}
        title={card?.name ?? t('cards.unknownCard')}
        action={
          <div className="flex items-center gap-2 self-start sm:self-auto">
            <Button asChild variant="outline">
              <Link to="/cards">
                <ArrowLeft size={16} className="mr-1.5" />
                {t('cards.backToCards')}
              </Link>
            </Button>
          </div>
        }
      />

      {cardLoading ? (
        <Skeleton className="mb-4 h-32 rounded-xl" />
      ) : (
        <>
          {isSnapshotView ? (
            <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 shadow-sm">
              <p className="font-medium">{t('dashboard.snapshotViewBadge', { period: selectedPeriodLabel })}</p>
              <p className="mt-1 text-amber-800">{t('dashboard.snapshotReadOnlyHint')}</p>
              <Button
                variant="outline"
                className="mt-3"
                onClick={() => setMonthViewMutation.mutate({ mode: 'current' })}
                disabled={setMonthViewMutation.isPending}
              >
                {t('cards.returnToCurrent')}
              </Button>
            </div>
          ) : null}

        <div className="mb-4 rounded-xl border border-border bg-card p-4 shadow-sm">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">{t('accounts.typeCreditCard')}</p>
              {isSnapshotView ? (
                <p className="mt-1 text-sm text-muted-foreground">{t('cards.snapshotDescription', { period: selectedPeriodLabel })}</p>
              ) : null}
            </div>

            <div className="flex flex-col gap-2 sm:min-w-[240px]">
              <label className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
                {t('cards.snapshotLabel')}
              </label>
              <select
                className="h-10 rounded-lg border border-border bg-card px-3 text-sm text-foreground"
                value={selectedSnapshotPeriod || '__current__'}
                onChange={(event) => {
                  const nextValue = event.target.value
                  if (nextValue === '__current__') {
                    setMonthViewMutation.mutate({ mode: 'current' })
                    return
                  }
                  const snapshot = snapshots.find((item) => item.period === nextValue)
                  if (!snapshot) return
                  const confirmed = window.confirm(
                    t('dashboard.snapshotConfirmMessage', { period: snapshot.period_label })
                  )
                  if (!confirmed) return
                  setMonthViewMutation.mutate({ mode: 'snapshot', period: snapshot.period })
                }}
                disabled={setMonthViewMutation.isPending}
              >
                {currentMonthDefined ? (
                  <option value="__current__">
                    {t('cards.currentOption', { period: currentPeriodLabel })}
                  </option>
                ) : (
                  <option value="__current__">{t('cards.setupOption')}</option>
                )}
                {snapshots.map((snapshot) => (
                  <option key={snapshot.period} value={snapshot.period}>
                    {t('cards.snapshotOption', { period: snapshot.period_label })}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
        </>
      )}

      {!currentMonthDefined ? (
        <div className="mb-4 rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground shadow-sm">
          <p className="font-medium text-foreground">{t('common.currentMonthLocked')}</p>
          <p className="mt-1">{t('transactions.currentMonthGuard')}</p>
        </div>
      ) : null}

      <div className="mb-4 rounded-xl border border-border bg-card p-3 shadow-sm md:p-4">
        <div className="flex min-w-0 flex-1 flex-col gap-2 md:flex-row md:flex-wrap md:items-end md:gap-3">
          <div className="relative w-full md:w-auto">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" size={16} />
            <Input
              type="text"
              placeholder={t('transactions.searchPlaceholder')}
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              className="h-[38px] w-full pl-9 text-sm md:w-[260px]"
            />
          </div>
          <select
            className="rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground focus:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/30"
            value={filterCategory}
            onChange={(event) => {
              setFilterCategory(event.target.value)
              setPage(1)
            }}
          >
            <option value="">{t('transactions.category')}: {t('transactions.all')}</option>
            <option value="__uncategorized__">{t('transactions.uncategorized')}</option>
            {categoriesList?.map((category) => (
              <option key={category.id} value={category.id}>{category.name}</option>
            ))}
          </select>
          {(filterCategory || searchInput) && (
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => {
                setFilterCategory('')
                setSearchInput('')
                setSearchQuery('')
                setPage(1)
              }}
            >
              {t('transactions.clearFilters')}
            </Button>
          )}
          {tagFilter && (
            <div className="flex items-center gap-1.5 rounded-lg border border-primary/10 bg-primary/5 px-3 py-1.5 text-xs font-medium text-primary">
              <span>{tagFilter}</span>
              <button
                onClick={() => setTagFilter(null)}
                className="ml-0.5 text-primary/60 hover:text-primary"
              >
                <X size={12} />
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="mb-4 overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        {isLoading ? (
          <div className="space-y-3 p-6">
            {Array.from({ length: 5 }).map((_, index) => (
              <Skeleton key={index} className="h-14 w-full" />
            ))}
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="border-b border-border hover:bg-transparent">
                <TableHead className="w-[40px] py-3 pl-4 pr-0">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={(element) => {
                      if (element) element.indeterminate = someSelected
                    }}
                    onChange={toggleSelectAll}
                    disabled={!editableMonth}
                    className="h-4 w-4 cursor-pointer rounded border-border accent-primary"
                  />
                </TableHead>
                <TableHead className="py-3 pl-2 text-xs font-medium text-muted-foreground">{t('transactions.description')}</TableHead>
                <TableHead className="hidden w-[180px] py-3 text-xs font-medium text-muted-foreground md:table-cell">{t('transactions.category')}</TableHead>
                <TableHead className="py-3 pr-5 text-right text-xs font-medium text-muted-foreground md:w-[180px]">{t('transactions.amount')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredItems.map((tx) => (
                <TableRow
                  key={tx.id}
                  className={`cursor-pointer border-b border-border last:border-0 hover:bg-muted ${selectedIds.has(tx.id) ? 'bg-primary/5' : ''}`}
                  onClick={() => {
                    if (!editableMonth) return
                    setEditingTx(tx)
                    setDialogOpen(true)
                  }}
                >
                  <TableCell className="w-[40px] py-2.5 pl-4 pr-0">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(tx.id)}
                      onChange={() => toggleSelect(tx.id)}
                      onClick={(event) => event.stopPropagation()}
                      disabled={!editableMonth}
                      className="h-4 w-4 cursor-pointer rounded border-border accent-primary"
                    />
                  </TableCell>
                  <TableCell className="max-w-0 py-2.5 pl-2">
                    <div className="flex items-center gap-2 md:gap-3">
                      <CategoryIcon icon={tx.category?.icon} color={tx.category?.color} size="lg" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-sm font-semibold text-foreground">{tx.description}</p>
                          {recurringList?.some((item) => item.description === tx.description && item.type === tx.type) && (
                            <span className="rounded-full border border-primary/10 bg-primary/5 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
                              {t('transactions.recurringBadge')}
                            </span>
                          )}
                          {(tx.attachment_count ?? 0) > 0 && (
                            <Paperclip size={12} className="shrink-0 text-muted-foreground" />
                          )}
                        </div>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {new Date(tx.date + 'T00:00:00').toLocaleDateString(locale)}
                        </p>
                        {tx.notes && (
                          <div className="mt-1 space-y-0.5">
                            {tx.notes.replace(/#[\w\u00C0-\u017E-]+/g, '').trim() && (
                              <p className="text-xs italic leading-snug text-muted-foreground">
                                {tx.notes.replace(/#[\w\u00C0-\u017E-]+/g, '').trim()}
                              </p>
                            )}
                            {parseHashtags(tx.notes).length > 0 && (
                              <div className="flex flex-wrap gap-1">
                                {parseHashtags(tx.notes).map((tag) => (
                                  <span
                                    key={tag}
                                    className="cursor-pointer rounded-full border border-primary/10 bg-primary/5 px-1.5 py-0 text-[11px] font-medium leading-5 text-primary transition-colors hover:bg-primary/10"
                                    onClick={(event) => {
                                      event.stopPropagation()
                                      setTagFilter(tag)
                                    }}
                                  >
                                    {tag}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="hidden py-2.5 md:table-cell">
                    {tx.category ? (
                      <span className="text-sm text-muted-foreground">{tx.category.name}</span>
                    ) : (
                      <span className="text-xs italic text-muted-foreground">{t('transactions.noCategory')}</span>
                    )}
                  </TableCell>
                  <TableCell className="py-2.5 pr-3 text-right md:pr-5">
                    <span className={`text-xs font-bold tabular-nums md:text-sm ${tx.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
                      {mask(`${tx.type === 'credit' ? '+' : '−'}${formatCurrency(Math.abs(Number(tx.amount)), tx.currency, locale)}`)}
                    </span>
                    {tx.amount_primary != null && tx.currency !== userCurrency && (
                      <div className="flex items-center justify-end gap-1">
                        {tx.fx_fallback && (
                          <span title={t('transactions.fxFallbackTooltip')}>
                            <AlertTriangle size={11} className="shrink-0 text-amber-500" />
                          </span>
                        )}
                        <span className="text-[10px] tabular-nums text-muted-foreground">
                          {mask(formatCurrency(Math.abs(tx.amount_primary), userCurrency, locale))}
                        </span>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {filteredItems.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4} className="py-16 text-center text-muted-foreground">
                    {filterCategory || searchQuery || tagFilter ? t('cards.resultsEmptyFiltered') : t('cards.resultsEmpty')}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </div>

      {totalPages > 1 && (
        <div className={`flex items-center justify-center gap-2 ${selectedIds.size > 0 ? 'pb-16' : ''}`}>
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            {t('transactions.previous')}
          </Button>
          <span className="text-sm text-muted-foreground">{page} / {totalPages}</span>
          <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
            {t('transactions.next')}
          </Button>
        </div>
      )}

      <div className={`fixed bottom-0 left-0 right-0 z-50 transition-transform duration-200 ease-out ${editableMonth && selectedIds.size > 0 ? 'translate-y-0' : 'translate-y-full'}`}>
        <div className="mx-auto max-w-2xl px-3 pb-4 md:px-4 md:pb-6">
          <div className="flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2.5 shadow-lg md:gap-3 md:px-5 md:py-3">
            <span className="whitespace-nowrap text-xs font-medium text-foreground md:text-sm">
              {t('transactions.selected', { count: selectedIds.size })}
            </span>
            <select
              className="min-w-0 flex-1 rounded-lg border border-border bg-card px-2 py-1.5 text-xs text-foreground focus:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/30 md:px-3 md:text-sm"
              value={bulkCategory}
              onChange={(event) => setBulkCategory(event.target.value)}
              disabled={!editableMonth}
            >
              <option value="">{t('transactions.selectCategory')}</option>
              {categoriesList?.map((category) => (
                <option key={category.id} value={category.id}>{category.name}</option>
              ))}
            </select>
            <Button
              size="sm"
              disabled={!editableMonth || !bulkCategory || bulkCategorizeMutation.isPending}
              onClick={() => {
                bulkCategorizeMutation.mutate({
                  ids: Array.from(selectedIds),
                  categoryId: bulkCategory || null,
                })
              }}
              className="shrink-0"
            >
              <Check size={14} className="mr-1" />
              {t('transactions.bulkCategorize')}
            </Button>
            <button
              onClick={() => {
                setSelectedIds(new Set())
                setBulkCategory('')
              }}
              className="shrink-0 p-1 text-muted-foreground hover:text-foreground"
            >
              <X size={16} />
            </button>
          </div>
        </div>
      </div>

      <TransactionDialog
        open={dialogOpen}
        onClose={() => {
          setDialogOpen(false)
          setEditingTx(null)
        }}
        transaction={editingTx}
        categories={categoriesList ?? []}
        accounts={card ? [card] : []}
        recurringMatch={editingTx ? recurringList?.find((item) => item.description === editingTx.description && item.type === editingTx.type) : undefined}
        onSave={(payload) => {
          if (!editingTx) return
          updateMutation.mutate({ id: editingTx.id, ...payload })
        }}
        onDelete={editingTx ? () => deleteMutation.mutate(editingTx.id) : undefined}
        loading={updateMutation.isPending || deleteMutation.isPending}
        error={updateMutation.error ? extractApiError(updateMutation.error) : null}
        isSynced={editingTx?.source === 'sync'}
      />
    </div>
  )
}
