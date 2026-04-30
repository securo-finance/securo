import { useState, useMemo, useEffect, useRef } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { transactions, categories as categoriesApi, accounts as accountsApi, recurring, payees as payeesApi, admin } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
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
import { Skeleton } from '@/components/ui/skeleton'
import { AlertTriangle, ArrowLeftRight, Check, Download, HelpCircle, Info, Paperclip, X } from 'lucide-react'
import type { Transaction } from '@/types'
import { PageHeader } from '@/components/page-header'
import { CategoryIcon } from '@/components/category-icon'
import { TransactionDialog, extractApiError, type SaveAction } from '@/components/transaction-dialog'
import { TransferDialog } from '@/components/transfer-dialog'
import { LinkTransferDialog } from '@/components/link-transfer-dialog'
import { TransactionsFilterBar } from '@/components/transactions-filter-bar'
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
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [filterAccountIds, setFilterAccountIds] = useState<string[]>([])
  const [filterCategoryIds, setFilterCategoryIds] = useState<string[]>(() => {
    const initial = searchParams.get('category_id')
    return initial ? [initial] : []
  })
  const [filterUncategorized, setFilterUncategorized] = useState<boolean>(false)
  const [filterFrom, setFilterFrom] = useState<string>('')
  const [filterTo, setFilterTo] = useState<string>('')
  const [searchInput, setSearchInput] = useState(() => searchParams.get('q') ?? '')
  const [searchQuery, setSearchQuery] = useState(() => searchParams.get('q') ?? '')
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingTx, setEditingTx] = useState<Transaction | null>(null)
  const [formResetKey, setFormResetKey] = useState(0)
  const [duplicateDraft, setDuplicateDraft] = useState<Partial<Transaction> | null>(null)
  const [filterPayee, setFilterPayee] = useState<string>(searchParams.get('payee_id') ?? '')
  const [tagFilters, setTagFilters] = useState<string[]>([])

  const addTagFilter = (tag: string) => {
    const normalized = tag.startsWith('#') ? tag : `#${tag}`
    setTagFilters(prev => (prev.includes(normalized) ? prev : [...prev, normalized]))
    setPage(1)
  }
  const removeTagFilter = (tag: string) => {
    setTagFilters(prev => prev.filter(t => t !== tag))
    setPage(1)
  }
  const clearTagFilters = () => {
    setTagFilters([])
    setPage(1)
  }
  const [exporting, setExporting] = useState(false)
  const [transferDialogOpen, setTransferDialogOpen] = useState(false)
  const [linkTransferDialogOpen, setLinkTransferDialogOpen] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bulkCategory, setBulkCategory] = useState<string>('')
  const [bulkTagInput, setBulkTagInput] = useState<string>('')
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null)
  const highlightId = searchParams.get('highlight')
  const highlightedRowRef = useRef<HTMLTableRowElement | null>(null)

  // Sync state from URL when navigating (e.g. from the command palette) while
  // the page is already mounted. Typing in the search box does not touch the
  // URL, so this effect only fires on genuine navigation events.
  useEffect(() => {
    const nextQ = searchParams.get('q') ?? ''
    setSearchInput(nextQ)
    setSearchQuery(nextQ)
    const tags = searchParams.get('tags');
    setTagFilters(tags ? tags.split(',') : []);
    setFilterPayee(searchParams.get('payee_id') ?? '')
    const categories = searchParams.get('category_id');
    setFilterCategoryIds(categories ? categories.split(',') : []);
    setFilterUncategorized(searchParams.get('uncategorized') === '1');
    const accounts = searchParams.get('account_id');
    setFilterAccountIds(accounts ? accounts.split(',') : []);
    setFilterFrom(searchParams.get('from') ?? '');
    setFilterTo(searchParams.get('to') ?? '');
    setPage(1)
  }, [searchParams])

  // Keep the URL in sync with the current filters, so that the current page can be
  // refreshed, bookmarked or shared.
  useEffect(() => {
    const params = new URLSearchParams(
      [
        ['q', searchQuery],
        ['tags', tagFilters.join(',')],
        ['payee_id', filterPayee],
        ['category_id', filterCategoryIds.join(',')],
        ['uncategorized', filterUncategorized ? '1' : ''],
        ['account_id', filterAccountIds.join(',')],
        ['from', filterFrom],
        ['to', filterTo],
      ].filter(([, v]) => v.length),
    );

    window.history.replaceState(
      null,
      '',
      params.size ? `?${params}` : window.location.pathname,
    );
  }, [
    searchQuery,
    tagFilters,
    filterPayee,
    filterCategoryIds,
    filterUncategorized,
    filterAccountIds,
    filterFrom,
    filterTo,
  ]);

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
  }, [page, filterAccountIds, filterCategoryIds, filterUncategorized, filterPayee, filterFrom, filterTo, searchQuery])

  // Scroll to and flash a highlighted row after navigation (e.g. opened via
  // the command palette). Re-runs whenever highlightId or the current data
  // set changes so that when results finish loading we animate the row.
  useEffect(() => {
    if (!highlightId) return
    const el = highlightedRowRef.current
    if (!el) return
    const raf = requestAnimationFrame(() => {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('securo-highlight-flash')
    })
    const timer = setTimeout(() => {
      el.classList.remove('securo-highlight-flash')
    }, 2500)
    return () => {
      cancelAnimationFrame(raf)
      clearTimeout(timer)
      el.classList.remove('securo-highlight-flash')
    }
  }, [highlightId, searchQuery, filterPayee, filterCategoryIds, page])

  const { data, isLoading } = useQuery({
    queryKey: ['transactions', page, filterAccountIds, filterCategoryIds, filterUncategorized, filterPayee, filterFrom, filterTo, searchQuery, tagFilters],
    queryFn: () =>
      transactions.list({
        page,
        limit: 20,
        account_ids: filterAccountIds.length > 0 ? filterAccountIds : undefined,
        category_ids: filterCategoryIds.length > 0 ? filterCategoryIds : undefined,
        payee_id: filterPayee || undefined,
        uncategorized: filterUncategorized ? true : undefined,
        from: filterFrom || undefined,
        to: filterTo || undefined,
        q: searchQuery || undefined,
        tags: tagFilters.length > 0 ? tagFilters : undefined,
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

  const { data: accountingModeData } = useQuery({
    queryKey: ['admin', 'accounting-mode'],
    queryFn: () => admin.accountingMode(),
    staleTime: 5 * 60 * 1000,
  })
  const isAccrual = accountingModeData?.mode === 'accrual'

  const invalidateAfterTxMutation = () => invalidateFinancialQueries(queryClient)

  const createMutation = useMutation({
    mutationFn: async (payload: { tx: Partial<Transaction>; recurringData?: { frequency: string; end_date?: string }; pendingFiles?: File[]; action?: SaveAction }) => {
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
    onSuccess: (_created, variables) => {
      invalidateAfterTxMutation()
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      toast.success(t('transactions.created'))
      if (variables.action === 'saveAndNew') {
        setDuplicateDraft(null)
        setFormResetKey(k => k + 1)
      } else if (variables.action === 'saveAndDuplicate') {
        setDuplicateDraft(variables.tx)
        setFormResetKey(k => k + 1)
      } else {
        setDialogOpen(false)
      }
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Transaction> & { id: string }) =>
      transactions.update(id, data),
    onSuccess: () => {
      invalidateAfterTxMutation()
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
      invalidateAfterTxMutation()
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
      invalidateAfterTxMutation()
      setSelectedIds(new Set())
      setBulkCategory('')
      toast.success(t('transactions.bulkSuccess', { count: result.updated }))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const bulkAddTagsMutation = useMutation({
    mutationFn: ({ ids, tags }: { ids: string[]; tags: string[] }) =>
      transactions.bulkAddTags(ids, tags),
    onSuccess: (result) => {
      invalidateAfterTxMutation()
      setSelectedIds(new Set())
      setBulkTagInput('')
      toast.success(t('transactions.bulkSuccess', { count: result.updated }))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const linkTransferMutation = useMutation({
    mutationFn: (ids: [string, string]) => transactions.linkTransfer(ids),
    onSuccess: () => {
      invalidateAfterTxMutation()
      queryClient.invalidateQueries({ queryKey: ['transfer-candidates'] })
      setLinkTransferDialogOpen(false)
      setSelectedIds(new Set())
      toast.success(t('transactions.linkTransferSuccess'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const unlinkTransferMutation = useMutation({
    mutationFn: (pairId: string) => transactions.unlinkTransfer(pairId),
    onSuccess: () => {
      invalidateAfterTxMutation()
      setDialogOpen(false)
      setEditingTx(null)
      toast.success(t('transactions.unlinkTransferSuccess'))
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
      invalidateAfterTxMutation()
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

  // Tag filtering is now applied server-side, so the visible list and the
  // page count both reflect the same filtered total — issue #88.
  const filteredItems = data?.items ?? []

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

  // Resolve the currently-selected transactions into a valid debit/credit pair
  // for the "Link as transfer" action. Returns null if the pair is invalid
  // (wrong count, same account, same type, or already linked).
  const linkablePair = useMemo(() => {
    if (selectedIds.size !== 2) return null
    const selected = (data?.items ?? []).filter(tx => selectedIds.has(tx.id))
    if (selected.length !== 2) return null
    if (selected.some(tx => tx.transfer_pair_id)) return null
    if (selected[0].account_id === selected[1].account_id) return null
    const debit = selected.find(tx => tx.type === 'debit')
    const credit = selected.find(tx => tx.type === 'credit')
    if (!debit || !credit) return null
    return { debit, credit }
  }, [selectedIds, data?.items])

  // Single-selection picker mode: when exactly one unlinked transaction is
  // selected, the user can search for its counterpart across all accounts.
  const linkAnchor = useMemo(() => {
    if (selectedIds.size !== 1) return null
    const selected = (data?.items ?? []).find(tx => selectedIds.has(tx.id))
    if (!selected) return null
    if (selected.transfer_pair_id) return null
    return selected
  }, [selectedIds, data?.items])

  const canOpenLinkDialog = !!linkablePair || !!linkAnchor
  const linkDisabledTooltip =
    !canOpenLinkDialog && selectedIds.size >= 2
      ? t('transactions.linkTransferInvalidPair')
      : undefined

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
                    account_ids: filterAccountIds.length > 0 ? filterAccountIds : undefined,
                    category_ids: filterCategoryIds.length > 0 ? filterCategoryIds : undefined,
                    uncategorized: filterUncategorized ? true : undefined,
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
            <Button variant="outline" onClick={() => setTransferDialogOpen(true)}>
              <ArrowLeftRight size={16} className="mr-1.5" />
              {t('transactions.transfer')}
            </Button>
            <Button onClick={() => { setEditingTx(null); setDialogOpen(true) }}>
              + {t('transactions.addManual')}
            </Button>
          </div>
        }
      />

      {/* Filters */}
      <TransactionsFilterBar
        searchInput={searchInput}
        onSearchChange={(v) => setSearchInput(v)}
        onSearchSubmit={(value) => {
          const trimmed = value.trim()
          // Tokenize submitted text. `#`-tokens become live tag filter
          // chips below the search bar (filtering applies immediately, no
          // Enter required). Non-`#` text remains as the free-text search
          // query (issue #88).
          const tokens = trimmed.split(/\s+/).filter(Boolean)
          const tags = tokens.filter(t => t.startsWith('#'))
          const text = tokens.filter(t => !t.startsWith('#')).join(' ')
          tags.forEach(addTagFilter)
          setSearchInput(text)
          setSearchQuery(text)
        }}
        filterAccountIds={filterAccountIds}
        onAccountIdsChange={(v) => { setFilterAccountIds(v); setPage(1) }}
        filterCategoryIds={filterCategoryIds}
        onCategoryIdsChange={(v) => { setFilterCategoryIds(v); setPage(1) }}
        filterUncategorized={filterUncategorized}
        onUncategorizedChange={(v) => { setFilterUncategorized(v); setPage(1) }}
        filterPayee={filterPayee}
        onPayeeChange={(v) => { setFilterPayee(v); setPage(1) }}
        filterFrom={filterFrom}
        filterTo={filterTo}
        onDateRangeChange={(from, to) => { setFilterFrom(from); setFilterTo(to); setPage(1) }}
        onClearAll={() => {
          setFilterFrom('')
          setFilterTo('')
          setFilterAccountIds([])
          setFilterCategoryIds([])
          setFilterUncategorized(false)
          setFilterPayee('')
          setSearchInput('')
          setSearchQuery('')
          clearTagFilters()
          setPage(1)
        }}
        accounts={accountsList ?? []}
        categories={categoriesList ?? []}
        payees={payeesList ?? []}
      />
      {tagFilters.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-1.5">
          {tagFilters.map(tag => (
            <span
              key={tag}
              className="inline-flex items-center gap-1.5 rounded-full border border-primary/15 bg-primary/5 px-3 py-1 text-xs font-medium text-primary"
            >
              <span>{tag}</span>
              <button
                onClick={() => removeTagFilter(tag)}
                className="ml-0.5 text-primary/60 hover:text-primary"
                aria-label={`Remove ${tag} filter`}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      {isAccrual && (filterFrom || filterTo) && (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
          <Info size={12} className="mt-0.5 shrink-0" />
          <span>{t('dashboard.accrualNote')}</span>
        </div>
      )}

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
                  ref={tx.id === highlightId ? highlightedRowRef : undefined}
                  className={`cursor-pointer hover:bg-muted border-b border-border last:border-0 ${selectedIds.has(tx.id) ? 'bg-primary/5' : ''}`}
                  onClick={() => { setEditingTx(tx); setDialogOpen(true) }}
                >
                  <TableCell className="py-2.5 pl-4 pr-0 w-[40px]">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(tx.id)}
                      onChange={() => toggleSelect(tx.id)}
                      onClick={(e) => e.stopPropagation()}
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
                              <span title={t('transactions.transferTooltip')}><HelpCircle className="h-3 w-3 text-blue-400" /></span>
                            </span>
                          )}
                          {recurringList?.some(r => r.description === tx.description && r.type === tx.type) && (
                            <span className="text-[10px] font-semibold uppercase tracking-wide text-primary bg-primary/5 border border-primary/10 px-1.5 py-0.5 rounded-full">
                              {t('transactions.recurringBadge')}
                            </span>
                          )}
                          {tx.installment_number != null && tx.total_installments != null && (
                            <span
                              className="inline-flex items-center text-[10px] font-bold tabular-nums text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-500/20 border border-amber-200 dark:border-amber-500/30 px-1.5 py-0.5 rounded-full"
                              title={tx.installment_total_amount != null
                                ? t('transactions.installmentTooltip', { count: tx.total_installments, total: tx.installment_total_amount })
                                : undefined}
                            >
                              {tx.installment_number}/{tx.total_installments}
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
                                    onClick={(e) => { e.stopPropagation(); addTagFilter(tag) }}
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
                    {getAccountName(accountsList?.find((a) => a.id === tx.account_id) ?? { name: '', display_name: null }) || (
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
        className={`fixed bottom-0 left-0 right-0 z-50 transition-transform duration-200 ease-out ${selectedIds.size > 0 ? 'translate-y-0' : 'translate-y-full'}`}
      >
        <div className="mx-auto max-w-5xl px-3 md:px-4 pb-4 md:pb-6">
          <div className="flex items-stretch gap-1.5 bg-card border border-border shadow-xl rounded-2xl p-2">
            {/* Selection count */}
            <div className="flex items-center gap-2 pl-3 pr-4 text-sm font-medium text-foreground whitespace-nowrap">
              <span className="inline-flex items-center justify-center size-6 rounded-full bg-primary/10 text-primary text-xs font-semibold">
                {selectedIds.size}
              </span>
              <span className="hidden sm:inline">{t('transactions.selected')}</span>
            </div>

            <div className="w-px bg-border/60 self-stretch" />

            {/* Categorize — fires on selection, no separate Apply button */}
            <select
              className="rounded-lg px-3 py-2 text-sm bg-transparent text-foreground hover:bg-muted/60 focus:outline-none focus-visible:bg-muted/60 cursor-pointer w-44 md:w-56"
              value={bulkCategory}
              onChange={(e) => {
                const next = e.target.value
                setBulkCategory(next)
                if (next) {
                  bulkCategorizeMutation.mutate({ ids: Array.from(selectedIds), categoryId: next })
                }
              }}
              disabled={bulkCategorizeMutation.isPending}
            >
              <option value="">{t('transactions.selectCategory')}</option>
              {categoriesList?.map((cat) => (
                <option key={cat.id} value={cat.id}>{cat.name}</option>
              ))}
            </select>

            <div className="w-px bg-border/60 self-stretch" />

            {/* Add tags inline */}
            <div className="flex items-center gap-1 px-1">
              <input
                type="text"
                value={bulkTagInput}
                onChange={(e) => setBulkTagInput(e.target.value)}
                placeholder={t('transactions.addTagsPlaceholder', '#tag…')}
                className="rounded-lg px-2.5 py-2 text-sm bg-transparent text-foreground placeholder:text-muted-foreground/70 focus:outline-none focus:bg-muted/60 w-28 md:w-40"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && bulkTagInput.trim()) {
                    e.preventDefault()
                    const tagList = bulkTagInput.trim().split(/[\s,]+/).filter(Boolean)
                    bulkAddTagsMutation.mutate({ ids: Array.from(selectedIds), tags: tagList })
                  }
                }}
              />
              <Button
                size="sm"
                variant="ghost"
                disabled={!bulkTagInput.trim() || bulkAddTagsMutation.isPending}
                onClick={() => {
                  const tagList = bulkTagInput.trim().split(/[\s,]+/).filter(Boolean)
                  if (tagList.length === 0) return
                  bulkAddTagsMutation.mutate({ ids: Array.from(selectedIds), tags: tagList })
                }}
                className="h-8 w-8 px-0 shrink-0"
                title={t('transactions.bulkAddTags', 'Add tags')}
              >
                <Check size={15} />
              </Button>
            </div>

            <div className="w-px bg-border/60 self-stretch" />

            {/* Link transfer */}
            <Button
              size="sm"
              variant="ghost"
              disabled={!canOpenLinkDialog}
              title={linkDisabledTooltip ?? t('transactions.linkAsTransfer')}
              onClick={() => setLinkTransferDialogOpen(true)}
              className="h-8 px-3 shrink-0 text-sm"
            >
              <ArrowLeftRight size={15} className="mr-1.5" />
              <span className="hidden lg:inline">{t('transactions.linkAsTransfer')}</span>
            </Button>

            <div className="ml-auto" />

            {/* Close */}
            <button
              onClick={() => { setSelectedIds(new Set()); setBulkCategory(''); setBulkTagInput('') }}
              className="text-muted-foreground hover:text-foreground p-2 shrink-0 self-center rounded-lg hover:bg-muted/60"
              title={t('common.close', 'Close')}
            >
              <X size={16} />
            </button>
          </div>
        </div>
      </div>

      {/* Link Transfer Dialog */}
      <LinkTransferDialog
        open={linkTransferDialogOpen}
        onClose={() => setLinkTransferDialogOpen(false)}
        debit={linkablePair?.debit ?? null}
        credit={linkablePair?.credit ?? null}
        anchor={linkAnchor}
        accounts={accountsList ?? []}
        onConfirm={(debitId, creditId) => {
          linkTransferMutation.mutate([debitId, creditId])
        }}
        loading={linkTransferMutation.isPending}
      />

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
        onClose={() => { setDialogOpen(false); setEditingTx(null); setDuplicateDraft(null) }}
        transaction={editingTx}
        duplicateDraft={duplicateDraft}
        formResetKey={formResetKey}
        categories={categoriesList ?? []}
        accounts={accountsList ?? []}
        recurringMatch={editingTx ? recurringList?.find(r => r.description === editingTx.description && r.type === editingTx.type) : undefined}
        onSave={(data, recurringData, pendingFiles, action) => {
          if (editingTx) {
            updateMutation.mutate({ id: editingTx.id, ...data })
          } else {
            createMutation.mutate({ tx: data, recurringData, pendingFiles, action })
          }
        }}
        onDelete={editingTx ? () => deleteMutation.mutate(editingTx.id) : undefined}
        onUnlinkTransfer={(pairId) => unlinkTransferMutation.mutate(pairId)}
        loading={createMutation.isPending || updateMutation.isPending || deleteMutation.isPending || unlinkTransferMutation.isPending}
        error={createMutation.error || updateMutation.error ? extractApiError(createMutation.error || updateMutation.error) : null}
        isSynced={editingTx?.source === 'sync'}
      />
    </div>
  )
}
