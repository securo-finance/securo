import { useState, useMemo, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { transactions, categories as categoriesApi, accounts as accountsApi, recurring, payees as payeesApi } from '@/lib/api'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { AlertTriangle, ArrowLeftRight, Check, Download, Paperclip, Search, X } from 'lucide-react'
import type { Transaction } from '@/types'
import { PageHeader } from '@/components/page-header'
import { CategoryIcon } from '@/components/category-icon'
import { TransactionDialog, extractApiError } from '@/components/transaction-dialog'
import { TransferDialog } from '@/components/transfer-dialog'
import { DatePickerInput } from '@/components/ui/date-picker-input'
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

export default function TransactionsPage() {
  const { t, i18n } = useTranslation()
  const [searchParams] = useSearchParams()
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const { data: currentMonthState } = useCurrentMonth()
  const currentMonthDefined = currentMonthState?.is_defined ?? false
  const isSnapshotView = currentMonthState?.is_snapshot_view ?? false
  const editableMonth = currentMonthDefined && !isSnapshotView
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [filterAccount, setFilterAccount] = useState<string>('')
  const [filterCategory, setFilterCategory] = useState<string>('')
  const [filterFrom, setFilterFrom] = useState<string>('')
  const [filterTo, setFilterTo] = useState<string>('')
  const [searchInput, setSearchInput] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingTx, setEditingTx] = useState<Transaction | null>(null)
  const [filterPayee, setFilterPayee] = useState<string>(searchParams.get('payee_id') ?? '')
  const [tagFilter, setTagFilter] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [transferDialogOpen, setTransferDialogOpen] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bulkCategory, setBulkCategory] = useState<string>('')
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null)

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearchQuery(searchInput)
      setPage(1)
    }, 300)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [searchInput])

  // Clear selection on page/filter change
  useEffect(() => {
    setSelectedIds(new Set())
    setBulkCategory('')
  }, [page, filterAccount, filterCategory, filterPayee, filterFrom, filterTo, searchQuery])

  useEffect(() => {
    if (!editableMonth) {
      setSelectedIds(new Set())
      setBulkCategory('')
    }
  }, [editableMonth])

  const { data, isLoading } = useQuery({
    queryKey: ['transactions', page, filterAccount, filterCategory, filterPayee, filterFrom, filterTo, searchQuery],
    queryFn: () =>
      transactions.list({
        page,
        limit: 20,
        account_id: filterAccount || undefined,
        category_id: filterCategory === '__uncategorized__' ? undefined : (filterCategory || undefined),
        payee_id: filterPayee || undefined,
        uncategorized: filterCategory === '__uncategorized__' ? true : undefined,
        from: filterFrom || undefined,
        to: filterTo || undefined,
        q: searchQuery || undefined,
      }),
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const { data: payeesList } = useQuery({
    queryKey: ['payees'],
    queryFn: payeesApi.list,
  })

  const { data: recurringList } = useQuery({
    queryKey: ['recurring'],
    queryFn: recurring.list,
  })

  const createMutation = useMutation({
    mutationFn: async (payload: { tx: Partial<Transaction>; recurringData?: { frequency: string; end_date?: string }; pendingFiles?: File[] }) => {
      const created = await transactions.create(payload.tx)
      if (payload.recurringData) {
        await recurring.create({
          description: payload.tx.description,
          amount: payload.tx.amount,
          currency: payload.tx.currency ?? userCurrency,
          type: payload.tx.type,
          frequency: payload.recurringData.frequency,
          start_date: payload.tx.date,
          end_date: payload.recurringData.end_date || undefined,
          category_id: payload.tx.category_id || undefined,
          account_id: payload.tx.account_id || undefined,
          skip_first: true,
        } as Record<string, unknown>)
      }
      if (payload.pendingFiles?.length) {
        await Promise.all(
          payload.pendingFiles.map(file => transactions.attachments.upload(created.id, file))
        )
      }
      return created
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      setDialogOpen(false)
      toast.success(t('transactions.created'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Transaction> & { id: string }) =>
      transactions.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      setDialogOpen(false)
      setEditingTx(null)
      toast.success(t('transactions.updated'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => transactions.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
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
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      setSelectedIds(new Set())
      setBulkCategory('')
      toast.success(t('transactions.bulkSuccess', { count: result.updated }))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const transferMutation = useMutation({
    mutationFn: (data: {
      from_account_id: string
      to_account_id: string
      amount: number
      date: string
      description: string
      notes?: string
      fx_rate?: number
    }) => transactions.createTransfer(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      setTransferDialogOpen(false)
      toast.success(t('transactions.transferCreated'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const filteredItems = useMemo(() => {
    if (!tagFilter || !data?.items) return data?.items ?? []
    return data.items.filter(tx =>
      tx.notes?.includes(tagFilter)
    )
  }, [data?.items, tagFilter])

  const toggleSelectAll = () => {
    if (!filteredItems.length) return
    const allSelected = filteredItems.every(tx => selectedIds.has(tx.id))
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(filteredItems.map(tx => tx.id)))
    }
  }

  const allSelected = filteredItems.length > 0 && filteredItems.every(tx => selectedIds.has(tx.id))
  const someSelected = filteredItems.some(tx => selectedIds.has(tx.id)) && !allSelected

  const totalPages = data ? Math.ceil(data.total / 20) : 0

  return (
    <div>
      <PageHeader
        section={t('transactions.section')}
        title={t('transactions.title')}
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              disabled={exporting}
              onClick={async () => {
                setExporting(true)
                try {
                  await transactions.export({
                    account_id: filterAccount || undefined,
                    category_id: filterCategory === '__uncategorized__' ? undefined : (filterCategory || undefined),
                    uncategorized: filterCategory === '__uncategorized__' ? true : undefined,
                    from: filterFrom || undefined,
                    to: filterTo || undefined,
                    q: searchQuery || undefined,
                  })
                  toast.success(t('transactions.exportSuccess'))
                } catch {
                  toast.error(t('transactions.exportError'))
                } finally {
                  setExporting(false)
                }
              }}
            >
              <Download size={16} className="mr-1.5" />
              {exporting ? t('transactions.exporting') : t('transactions.exportCsv')}
            </Button>
            <Button
              variant="outline"
              onClick={() => setTransferDialogOpen(true)}
              disabled={!editableMonth}
              title={!currentMonthDefined ? t('common.currentMonthLocked') : isSnapshotView ? t('common.snapshotReadOnlyLocked') : undefined}
            >
              <ArrowLeftRight size={16} className="mr-1.5" />
              {t('transactions.transfer')}
            </Button>
            <Button
              onClick={() => { setEditingTx(null); setDialogOpen(true) }}
              disabled={!editableMonth}
              title={!currentMonthDefined ? t('common.currentMonthLocked') : isSnapshotView ? t('common.snapshotReadOnlyLocked') : undefined}
            >
              + {t('transactions.addManual')}
            </Button>
          </div>
        }
      />

      {!currentMonthDefined ? (
        <div className="mb-4 rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground shadow-sm">
          <p className="font-medium text-foreground">{t('common.currentMonthLocked')}</p>
          <p className="mt-1">{t('transactions.currentMonthGuard')}</p>
        </div>
      ) : isSnapshotView ? (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 shadow-sm">
          <p className="font-medium">{t('common.snapshotReadOnlyTitle', { period: currentMonthState?.selected_period_label })}</p>
          <p className="mt-1">{t('common.snapshotReadOnlyHint')}</p>
        </div>
      ) : null}

      {/* Filters */}
      <div className="bg-card rounded-xl border border-border shadow-sm p-3 md:p-4 mb-4">
        <div className="flex flex-col gap-2 md:flex-row md:flex-wrap md:items-end md:gap-3">
          <div className="relative w-full md:w-auto">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" size={16} />
            <Input
              type="text"
              placeholder={t('transactions.searchPlaceholder')}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="pl-9 w-full md:w-[240px] h-[38px] text-sm"
            />
          </div>
          <div className="grid grid-cols-2 gap-2 md:flex md:gap-3">
            <select
              className="border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px] min-w-0"
              value={filterAccount}
              onChange={(e) => { setFilterAccount(e.target.value); setPage(1) }}
            >
              <option value="">{t('transactions.account')}: {t('transactions.all')}</option>
              {accountsList?.map((acc) => (
                <option key={acc.id} value={acc.id}>{acc.name}</option>
              ))}
            </select>
            <select
              className="border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px] min-w-0"
              value={filterCategory}
              onChange={(e) => { setFilterCategory(e.target.value); setPage(1) }}
            >
              <option value="">{t('transactions.category')}: {t('transactions.all')}</option>
              <option value="__uncategorized__">{t('transactions.uncategorized')}</option>
              {categoriesList?.map((cat) => (
                <option key={cat.id} value={cat.id}>{cat.name}</option>
              ))}
            </select>
            <select
              className="border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px] min-w-0"
              value={filterPayee}
              onChange={(e) => { setFilterPayee(e.target.value); setPage(1) }}
            >
              <option value="">{t('payees.payee')}: {t('transactions.all')}</option>
              {payeesList?.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-2 md:flex md:gap-3">
            <div className="flex items-center gap-2">
              <label className="hidden md:inline text-sm text-muted-foreground">{t('transactions.from')}</label>
              <DatePickerInput
                value={filterFrom}
                onChange={(v) => { setFilterFrom(v); setPage(1) }}
                placeholder={t('transactions.from')}
              />
            </div>
            <div className="flex items-center gap-2">
              <label className="hidden md:inline text-sm text-muted-foreground">{t('transactions.to')}</label>
              <DatePickerInput
                value={filterTo}
                onChange={(v) => { setFilterTo(v); setPage(1) }}
                placeholder={t('transactions.to')}
              />
            </div>
          </div>
          {(filterFrom || filterTo || filterAccount || filterCategory || filterPayee || searchInput) && (
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => { setFilterFrom(''); setFilterTo(''); setFilterAccount(''); setFilterCategory(''); setFilterPayee(''); setSearchInput(''); setSearchQuery(''); setPage(1) }}
            >
              {t('transactions.clearFilters')}
            </Button>
          )}
          {tagFilter && (
            <div className="flex items-center gap-1.5 px-3 py-1.5 bg-primary/5 border border-primary/10 rounded-lg text-xs text-primary font-medium">
              <span>{tagFilter}</span>
              <button
                onClick={() => setTagFilter(null)}
                className="text-primary/60 hover:text-primary ml-0.5"
              >
                <X size={12} />
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden mb-4">
        {isLoading ? (
          <div className="p-6 space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
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
                    ref={(el) => { if (el) el.indeterminate = someSelected }}
                    onChange={toggleSelectAll}
                    disabled={!editableMonth}
                    className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
                  />
                </TableHead>
                <TableHead className="text-xs font-medium text-muted-foreground py-3 pl-2">{t('transactions.description')}</TableHead>
                <TableHead className="hidden md:table-cell text-xs font-medium text-muted-foreground py-3 w-[180px]">{t('transactions.category')}</TableHead>
                <TableHead className="hidden lg:table-cell text-xs font-medium text-muted-foreground py-3 w-[160px]">{t('transactions.account')}</TableHead>
                <TableHead className="text-xs font-medium text-muted-foreground py-3 pr-5 text-right w-[120px] md:w-[180px]">{t('transactions.amount')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredItems.map((tx) => (
                <TableRow
                  key={tx.id}
                  className={`cursor-pointer hover:bg-muted border-b border-border last:border-0 ${selectedIds.has(tx.id) ? 'bg-primary/5' : ''}`}
                  onClick={() => {
                    if (!editableMonth) return
                    setEditingTx(tx)
                    setDialogOpen(true)
                  }}
                >
                  <TableCell className="py-2.5 pl-4 pr-0 w-[40px]">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(tx.id)}
                      onChange={() => toggleSelect(tx.id)}
                      onClick={(e) => e.stopPropagation()}
                      disabled={!editableMonth}
                      className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
                    />
                  </TableCell>
                  <TableCell className="py-2.5 pl-2 max-w-0">
                    <div className="flex items-center gap-2 md:gap-3">
                      <CategoryIcon icon={tx.category?.icon} color={tx.category?.color} size="lg" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-semibold text-foreground truncate">{tx.description}</p>
                          {!!tx.transfer_pair_id && (
                            <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-blue-600 bg-blue-50 border border-blue-200 px-1.5 py-0.5 rounded-full">
                              <ArrowLeftRight className="h-3 w-3" />
                              {t('transactions.transfer')}
                            </span>
                          )}
                          {recurringList?.some(r => r.description === tx.description && r.type === tx.type) && (
                            <span className="text-[10px] font-semibold uppercase tracking-wide text-primary bg-primary/5 border border-primary/10 px-1.5 py-0.5 rounded-full">
                              {t('transactions.recurringBadge')}
                            </span>
                          )}
                          {(tx.attachment_count ?? 0) > 0 && (
                            <Paperclip size={12} className="text-muted-foreground shrink-0" />
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">{new Date(tx.date + 'T00:00:00').toLocaleDateString(locale)}</p>
                        {tx.notes && (
                          <div className="mt-1 space-y-0.5">
                            {tx.notes.replace(/#[\w\u00C0-\u017E-]+/g, '').trim() && (
                              <p className="text-xs text-muted-foreground italic leading-snug">
                                {tx.notes.replace(/#[\w\u00C0-\u017E-]+/g, '').trim()}
                              </p>
                            )}
                            {parseHashtags(tx.notes).length > 0 && (
                              <div className="flex flex-wrap gap-1">
                                {parseHashtags(tx.notes).map((tag) => (
                                  <span
                                    key={tag}
                                    className="inline-block text-[11px] font-medium bg-primary/5 text-primary border border-primary/10 px-1.5 py-0 rounded-full leading-5 cursor-pointer hover:bg-primary/10 transition-colors"
                                    onClick={(e) => { e.stopPropagation(); setTagFilter(tag) }}
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
                  <TableCell className="hidden md:table-cell py-2.5">
                    {tx.category ? (
                      <span className="text-sm text-muted-foreground">{tx.category.name}</span>
                    ) : (
                      <span className="text-xs text-muted-foreground italic">{t('transactions.noCategory')}</span>
                    )}
                  </TableCell>
                  <TableCell className="hidden lg:table-cell py-2.5 text-sm text-muted-foreground">
                    {accountsList?.find((a) => a.id === tx.account_id)?.name ?? (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="py-2.5 pr-3 md:pr-5 text-right">
                    <span className={`text-xs md:text-sm font-bold tabular-nums ${tx.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
                      {mask(`${tx.type === 'credit' ? '+' : '−'}${formatCurrency(Math.abs(Number(tx.amount)), tx.currency, locale)}`)}
                    </span>
                    {tx.amount_primary != null && tx.currency !== userCurrency && (
                      <div className="flex items-center justify-end gap-1">
                        {tx.fx_fallback && (
                          <span title={t('transactions.fxFallbackTooltip')}><AlertTriangle size={11} className="text-amber-500 shrink-0" /></span>
                        )}
                        <span className="text-[10px] text-muted-foreground tabular-nums">
                          {mask(formatCurrency(Math.abs(tx.amount_primary), userCurrency, locale))}
                        </span>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {filteredItems.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-16 text-muted-foreground">
                    {t('transactions.noResults')}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className={`flex items-center justify-center gap-2 ${selectedIds.size > 0 ? 'pb-16' : ''}`}>
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage(page - 1)}
          >
            {t('transactions.previous')}
          </Button>
          <span className="text-sm text-muted-foreground">
            {page} / {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => setPage(page + 1)}
          >
            {t('transactions.next')}
          </Button>
        </div>
      )}

      {/* Bulk Action Bar */}
      <div
        className={`fixed bottom-0 left-0 right-0 z-50 transition-transform duration-200 ease-out ${editableMonth && selectedIds.size > 0 ? 'translate-y-0' : 'translate-y-full'}`}
      >
        <div className="mx-auto max-w-2xl px-3 md:px-4 pb-4 md:pb-6">
          <div className="flex items-center gap-2 md:gap-3 bg-card border border-border shadow-lg rounded-xl px-3 md:px-5 py-2.5 md:py-3">
            <span className="text-xs md:text-sm font-medium text-foreground whitespace-nowrap">
              {t('transactions.selected', { count: selectedIds.size })}
            </span>
            <select
              className="border border-border rounded-lg px-2 md:px-3 py-1.5 text-xs md:text-sm bg-card text-foreground focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px] flex-1 min-w-0"
              value={bulkCategory}
              onChange={(e) => setBulkCategory(e.target.value)}
              disabled={!editableMonth}
            >
              <option value="">{t('transactions.selectCategory')}</option>
              {categoriesList?.map((cat) => (
                <option key={cat.id} value={cat.id}>{cat.name}</option>
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
              onClick={() => { setSelectedIds(new Set()); setBulkCategory('') }}
              className="text-muted-foreground hover:text-foreground p-1 shrink-0"
            >
              <X size={16} />
            </button>
          </div>
        </div>
      </div>

      {/* Transfer Dialog */}
      <TransferDialog
        open={transferDialogOpen}
        onClose={() => setTransferDialogOpen(false)}
        accounts={accountsList ?? []}
        onSave={(data) => transferMutation.mutate(data)}
        loading={transferMutation.isPending}
      />

      {/* Add/Edit Dialog */}
      <TransactionDialog
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setEditingTx(null) }}
        transaction={editingTx}
        categories={categoriesList ?? []}
        accounts={accountsList ?? []}
        recurringMatch={editingTx ? recurringList?.find(r => r.description === editingTx.description && r.type === editingTx.type) : undefined}
        onSave={(data, recurringData, pendingFiles) => {
          if (editingTx) {
            updateMutation.mutate({ id: editingTx.id, ...data })
          } else {
            createMutation.mutate({ tx: data, recurringData, pendingFiles })
          }
        }}
        onDelete={editingTx ? () => deleteMutation.mutate(editingTx.id) : undefined}
        loading={createMutation.isPending || updateMutation.isPending || deleteMutation.isPending}
        error={createMutation.error || updateMutation.error ? extractApiError(createMutation.error || updateMutation.error) : null}
        isSynced={editingTx?.source === 'sync'}
      />
    </div>
  )
}
