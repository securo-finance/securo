import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { Query } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { payees as payeesApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
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
  DropdownMenuSeparator,
  DropdownMenuLabel,
  DropdownMenuCheckboxItem,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
  DropdownMenuPortal,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { PageHeader } from '@/components/page-header'
import { PayeeDetailDialog } from '@/components/payee-detail-dialog'
import { PayeeFormDialog } from '@/components/payee-form-dialog'
import { calculateRangeSelection } from '@/lib/selection-utils'
import { Search, Star, Merge, Trash2, ListFilter, X, Check, Pencil } from 'lucide-react'
import { useWorkspace } from '@/contexts/workspace-context'
import type { Payee } from '@/types'


export default function PayeesPage() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const { canWrite } = useWorkspace()
  // No entry for an unset type: most rows come from sync, which cannot know
  // a legal nature from a bank descriptor, and a badge reading "unknown" on
  // hundreds of rows is noise rather than information.
  const typeLabels: Record<string, string> = {
    person: t('payees.typePerson'),
    company: t('payees.typeCompany'),
  }
  const queryClient = useQueryClient()
  const [search, setSearch] = useState(() => searchParams.get('q') ?? '')
  const [searchQuery, setSearchQuery] = useState(() => searchParams.get('q') ?? '')
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingPayee, setEditingPayee] = useState<Payee | null>(null)
  const [summaryPayee, setSummaryPayee] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [lastSelectedId, setLastSelectedId] = useState<string | null>(null)
  const [mergeDialogOpen, setMergeDialogOpen] = useState(false)
  const [mergeTargetId, setMergeTargetId] = useState<string>('')
  const [filterType, setFilterType] = useState(() => searchParams.get('type') ?? '')
  const [filterFavorites, setFilterFavorites] = useState(() => searchParams.get('is_favorite') === 'true')
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [payeesToDelete, setPayeesToDelete] = useState<string[]>([])
  const prevSearchRef = useRef<string | null>(null)
  const prevFiltersRef = useRef<string | null>(null)
  const filterKey = `${searchQuery}|${filterType}|${filterFavorites}`
  // Seeded from the URL so a link to page 3 lands on page 3. Page size is a
  // preference rather than a location, so it lives in storage instead.
  const [page, setPage] = useState(() => Math.max(1, Number(searchParams.get('page')) || 1))
  const [pageSize, setPageSize] = useState<number>(() => {
    try {
      const stored = localStorage.getItem('securo.payees.pageSize')
      return stored ? Number(stored) : 20
    } catch {
      return 20
    }
  })

  // Sync state from URL when navigating
  useEffect(() => {
    const searchStr = searchParams.toString()
    if (prevSearchRef.current === searchStr) return
    prevSearchRef.current = searchStr

    const nextQ = searchParams.get('q') ?? ''
    const nextType = searchParams.get('type') ?? ''
    const nextFavorites = searchParams.get('is_favorite') === 'true'
    setSearch(nextQ)
    setSearchQuery(nextQ)
    setFilterType(nextType)
    setFilterFavorites(nextFavorites)
    setPage(Math.max(1, Number(searchParams.get('page')) || 1))
    // Filters and page arrived together, so this is not a filter *change*.
    // Priming the ref stops the reset effect below from throwing away the
    // page the same URL just asked for.
    prevFiltersRef.current = `${nextQ}|${nextType}|${nextFavorites}`
  }, [searchParams])

  // Sync states back to URL searchParams
  useEffect(() => {
    const params = new URLSearchParams(
      [
        ['q', searchQuery],
        ['type', filterType],
        ['is_favorite', filterFavorites ? 'true' : ''],
        ['page', page > 1 ? String(page) : ''],
      ].filter(([, v]) => v && v.length),
    )

    window.history.replaceState(
      null,
      '',
      params.size ? `?${params}` : window.location.pathname,
    )
  }, [searchQuery, filterType, filterFavorites, page])

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearchQuery(search)
    }, 300)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [search])

  // Clear selection and go back to the first page when the filters change.
  // Guarded rather than fired on every run: this effect also runs on mount and
  // on a URL-driven filter change, and an unguarded reset would discard the
  // `?page=` those two cases carry.
  useEffect(() => {
    if (prevFiltersRef.current === null || prevFiltersRef.current === filterKey) {
      prevFiltersRef.current = filterKey
      return
    }
    prevFiltersRef.current = filterKey
    setSelectedIds(new Set())
    setLastSelectedId(null)
    setPage(1)
  }, [filterKey])

  const { data: payeesList, isLoading } = useQuery({
    queryKey: ['payees', searchQuery, filterType, filterFavorites],
    queryFn: () => payeesApi.list({
      q: searchQuery || undefined,
      type: filterType || undefined,
      is_favorite: filterFavorites || undefined,
    }),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => payeesApi.delete(id),
    onSuccess: (_, id) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      setDialogOpen(false)
      setDeleteDialogOpen(false)
      if (editingPayee?.id === id) {
        setEditingPayee(null)
      }
      setSelectedIds(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
      if (summaryPayee === id) {
        setSummaryPayee(null)
      }
      toast.success(t('payees.deleted'))
    },
    onError: () => toast.error(t('common.error')),
  })

  // The star is the one control on this page people click in bursts, and an
  // invalidate-only mutation leaves it unchanged until the refetch lands. On a
  // slow link that reads as the click having missed, so this one patches the
  // cache up front and puts it back if the server disagrees.
  const favoriteMutation = useMutation({
    mutationFn: ({ id, is_favorite }: { id: string; is_favorite: boolean }) =>
      payeesApi.update(id, { is_favorite }),
    onMutate: async ({ id, is_favorite }) => {
      // Array-shaped caches only. `['payees']` is a prefix that also matches
      // `['payees', id, 'summary']`, whose data is an object, and mapping over
      // that would throw. Every page that lists payees caches an array under
      // this prefix, so they all stay in step for free.
      const listFilter = {
        queryKey: ['payees'],
        predicate: (query: Query) => Array.isArray(query.state.data),
      }
      await queryClient.cancelQueries(listFilter)
      const snapshots = queryClient.getQueriesData<Payee[]>(listFilter)
      queryClient.setQueriesData<Payee[]>(listFilter, (old) =>
        old?.map((payee) => (payee.id === id ? { ...payee, is_favorite } : payee)),
      )
      return { snapshots }
    },
    onError: (_error, _variables, context) => {
      for (const [key, data] of context?.snapshots ?? []) queryClient.setQueryData(key, data)
      toast.error(t('payees.favoriteError'))
    },
    // Prefix invalidation is safe here: it only marks stale and refetches.
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['payees'] })
    },
  })

  const mergeMutation = useMutation({
    mutationFn: ({ targetId, sourceIds }: { targetId: string; sourceIds: string[] }) =>
      payeesApi.merge(targetId, sourceIds),
    onSuccess: (result, variables) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      setMergeDialogOpen(false)
      setSelectedIds(new Set())
      setLastSelectedId(null)
      setMergeTargetId('')
      if (summaryPayee && variables.sourceIds.includes(summaryPayee)) {
        setSummaryPayee(null)
      }
      toast.success(t('payees.merged', { count: result.transactions_reassigned }))
    },
    onError: () => toast.error(t('common.error')),
  })

  const bulkDeleteMutation = useMutation({
    mutationFn: (ids: string[]) => payeesApi.bulkDelete(ids),
    onSuccess: (result, deletedIds) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      setDeleteDialogOpen(false)
      setSelectedIds(new Set())
      setLastSelectedId(null)
      if (summaryPayee && deletedIds.includes(summaryPayee)) {
        setSummaryPayee(null)
      }
      toast.success(t('payees.deletedMultiple', { count: result.deleted, defaultValue: `${result.deleted} payees deleted` }))
    },
    onError: () => toast.error(t('common.error')),
  })

  const openCreate = () => {
    setEditingPayee(null)
    setDialogOpen(true)
  }

  const openEdit = (payee: Payee) => {
    setEditingPayee(payee)
    setDialogOpen(true)
  }

  const filtered = payeesList ?? []
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const pageItems = filtered.slice((safePage - 1) * pageSize, safePage * pageSize)

  // Deleting the last page's contents strands `page` past the end. `safePage`
  // already covers what renders; this keeps the state and the URL honest.
  useEffect(() => {
    if (page > totalPages) setPage(totalPages)
  }, [page, totalPages])

  // Resolved against the whole filtered list, not the page, so the dialog
  // survives paging away from the row that opened it.
  const detailPayee = summaryPayee ? filtered.find(payee => payee.id === summaryPayee) ?? null : null

  // Every selection gesture is scoped to what the user can actually see.
  // Against the full list, shift-clicking across a page boundary would sweep
  // up rows nobody looked at — and the neighbouring button is a bulk delete.
  const toggleSelect = (id: string, isShiftKey: boolean = false) => {
    setSelectedIds(prev =>
      calculateRangeSelection(prev, lastSelectedId, id, pageItems, isShiftKey)
    )
    setLastSelectedId(id)
  }

  const allSelected = pageItems.length > 0 && pageItems.every(payee => selectedIds.has(payee.id))
  const someSelected = pageItems.some(payee => selectedIds.has(payee.id)) && !allSelected

  const toggleSelectAll = () => {
    if (!pageItems.length) return
    setSelectedIds(prev => {
      const next = new Set(prev)
      // Add or remove only this page: the selection itself spans pages, so
      // clearing it wholesale would silently drop rows picked elsewhere.
      for (const payee of pageItems) {
        if (allSelected) next.delete(payee.id)
        else next.add(payee.id)
      }
      return next
    })
  }

  return (
    <div>
      <PageHeader
        section={t('payees.section')}
        title={t('payees.title')}
        action={
          canWrite ? (
            <div className="flex items-center gap-2">
              {selectedIds.size >= 2 && (
                <div className="flex items-center gap-2">
                  <Button variant="outline" onClick={() => { setMergeTargetId(''); setMergeDialogOpen(true) }}>
                    <Merge size={16} className="mr-1.5" />
                    {t('payees.merge')} ({selectedIds.size})
                  </Button>
                  <Button variant="destructive" onClick={() => {
                    setPayeesToDelete(Array.from(selectedIds))
                    setDeleteDialogOpen(true)
                  }} disabled={bulkDeleteMutation.isPending}>
                    <Trash2 size={16} className="mr-1.5" />
                    {t('common.delete')} ({selectedIds.size})
                  </Button>
                </div>
              )}
              <Button onClick={openCreate}>
                + {t('payees.add')}
              </Button>
            </div>
          ) : undefined
        }
      />

      {/* Search & Filters */}
      <div
        className={cn(
          'group/filterbar rounded-xl border border-border bg-card shadow-sm transition-colors mb-4',
          'focus-within:border-primary/40 focus-within:ring-[3px] focus-within:ring-primary/10',
        )}
      >
        {/* Top row: search input + controls */}
        <div className="flex items-center gap-1.5 px-2 py-1.5">
          <div className="relative flex min-w-0 flex-1 items-center gap-1 px-2.5 py-1 min-h-9">
            <Search size={15} className="pointer-events-none shrink-0 text-muted-foreground/70" />
            <input
              type="text"
              placeholder={t('payees.searchPlaceholder')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="flex-1 bg-transparent px-1.5 text-[13.5px] outline-none placeholder:text-muted-foreground/75"
            />
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-1 pl-1">
            {(search || filterType || filterFavorites) && (
              <button
                type="button"
                onClick={() => {
                  setSearch('')
                  setSearchQuery('')
                  setFilterType('')
                  setFilterFavorites(false)
                }}
                className="hidden h-7 items-center rounded-md px-2 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:inline-flex"
              >
                {t('transactions.clearFilters')}
              </button>
            )}

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  className={cn(
                    'inline-flex h-8 items-center gap-1.5 rounded-md border border-border/80 bg-card px-2.5 text-[12px] font-medium text-muted-foreground transition-colors',
                    'hover:bg-muted hover:text-foreground',
                    (filterType || filterFavorites) && 'border-primary/30 text-primary hover:text-primary',
                  )}
                >
                  <ListFilter size={13} />
                  <span>{t('transactions.filtersBar.filters')}</span>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-[200px] p-1 bg-card border border-border rounded-xl shadow-md">
                <DropdownMenuLabel className="px-2 py-1 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {t('transactions.filtersBar.filterBy') || 'Filter By'}
                </DropdownMenuLabel>
                
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger className="gap-2 rounded-lg px-2 py-1.5 text-xs cursor-pointer hover:bg-muted transition-colors">
                    <ListFilter size={13} className="text-muted-foreground shrink-0" />
                    <span className="flex-1 text-left">{t('payees.type')}</span>
                    {filterType && (
                      <span className="text-[10px] bg-primary/10 text-primary px-1.5 py-0.5 rounded-full font-medium">
                        {typeLabels[filterType]}
                      </span>
                    )}
                  </DropdownMenuSubTrigger>
                  <DropdownMenuPortal>
                    <DropdownMenuSubContent className="w-[180px] p-1 bg-card border border-border rounded-xl shadow-md">
                      {[
                        { value: '', label: t('payees.allTypes', 'All Types') },
                        { value: 'person', label: t('payees.typePerson') },
                        { value: 'company', label: t('payees.typeCompany') },
                      ].map((opt) => (
                        <DropdownMenuItem
                          key={opt.value || 'all'}
                          onSelect={() => setFilterType(opt.value)}
                          className={cn(
                            'gap-2 rounded-lg px-2 py-1.5 text-xs cursor-pointer hover:bg-muted transition-colors',
                            filterType === opt.value && 'bg-primary/5 text-primary hover:bg-primary/5',
                          )}
                        >
                          <span className="min-w-0 flex-1 truncate text-left">
                            {opt.label}
                          </span>
                          {filterType === opt.value && (
                            <Check size={13} className="text-primary" />
                          )}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuSubContent>
                  </DropdownMenuPortal>
                </DropdownMenuSub>

                <DropdownMenuCheckboxItem
                  checked={filterFavorites}
                  onCheckedChange={setFilterFavorites}
                  className="gap-2 rounded-lg px-2 py-1.5 text-xs cursor-pointer hover:bg-muted transition-colors"
                >
                  <Star size={13} className={cn("mr-1 shrink-0", filterFavorites ? "fill-amber-400 text-amber-400" : "text-muted-foreground")} />
                  <span className="flex-1 text-left">{t('payees.favoritesOnly')}</span>
                </DropdownMenuCheckboxItem>

                {(filterType || filterFavorites) && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onSelect={() => {
                        setFilterType('')
                        setFilterFavorites(false)
                      }}
                      className="gap-2 rounded-lg px-2 py-1.5 text-xs cursor-pointer hover:bg-muted text-destructive hover:text-destructive focus:text-destructive focus:bg-destructive/5 font-medium"
                    >
                      <X size={13} className="mr-1 shrink-0" />
                      <span>{t('transactions.clearFilters')}</span>
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        {/* Bottom row: active filter chips */}
        {(filterType || filterFavorites) && (
          <div className="flex flex-wrap items-center gap-1.5 border-t border-border/60 px-2 py-1.5">
            {filterType && (
              <button
                type="button"
                onClick={() => setFilterType('')}
                className="group inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border border-border/80 bg-muted/50 pl-2 pr-1.5 text-[11.5px] text-foreground transition-colors hover:border-destructive/40 hover:bg-destructive/5"
              >
                <span className="flex items-center text-muted-foreground group-hover:text-destructive">
                  <ListFilter size={12} />
                </span>
                <span className="text-muted-foreground">{t('payees.type')}:</span>
                <span className="max-w-[140px] truncate font-medium text-foreground">
                  {typeLabels[filterType]}
                </span>
                <span className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground/70 group-hover:text-destructive">
                  <X size={11} />
                </span>
              </button>
            )}

            {filterFavorites && (
              <button
                type="button"
                onClick={() => setFilterFavorites(false)}
                className="group inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border border-border/80 bg-muted/50 pl-2 pr-1.5 text-[11.5px] text-foreground transition-colors hover:border-destructive/40 hover:bg-destructive/5"
              >
                <span className="flex items-center text-amber-400 group-hover:text-destructive">
                  <Star size={12} className="fill-amber-400" />
                </span>
                <span className="text-muted-foreground">{t('payees.favoritesOnly')}</span>
                <span className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground/70 group-hover:text-destructive">
                  <X size={11} />
                </span>
              </button>
            )}
          </div>
        )}
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
          <>
          <Table>
            <TableHeader>
              <TableRow className="border-b border-border hover:bg-transparent">
                 {canWrite && (
                   <TableHead className="w-[40px] py-3 pl-4 pr-0">
                     <input
                       type="checkbox"
                       checked={allSelected}
                       ref={(el) => { if (el) el.indeterminate = someSelected }}
                       onChange={toggleSelectAll}
                       className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
                     />
                   </TableHead>
                 )}
                <TableHead className="text-xs font-medium text-muted-foreground py-3 w-[32px]" />
                <TableHead className="text-xs font-medium text-muted-foreground py-3 w-full max-w-0">{t('payees.name')}</TableHead>
                <TableHead className="hidden md:table-cell text-xs font-medium text-muted-foreground py-3 w-[120px]">{t('payees.type')}</TableHead>
                <TableHead className="text-xs font-medium text-muted-foreground py-3 text-right w-[120px]">{t('payees.transactionCount')}</TableHead>
                {canWrite && <TableHead className="w-[100px]" />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {pageItems.map((payee) => (
                <TableRow
                  key={payee.id}
                  className={`cursor-pointer hover:bg-muted border-b border-border last:border-0 ${
                    summaryPayee === payee.id ? 'bg-muted/80 font-medium' : selectedIds.has(payee.id) ? 'bg-primary/5' : ''
                  }`}
                  onClick={() => setSummaryPayee(payee.id)}
                >
                  {canWrite && (
                    <TableCell className="py-2.5 pl-4 pr-0 w-[40px]">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(payee.id)}
                        onChange={() => {}}
                        onClick={(e) => {
                          e.stopPropagation()
                          toggleSelect(payee.id, e.shiftKey)
                        }}
                        className="h-4 w-4 rounded border-border accent-primary cursor-pointer"
                      />
                    </TableCell>
                  )}
                  <TableCell className="py-2.5 w-[32px]">
                    {canWrite ? (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          favoriteMutation.mutate({ id: payee.id, is_favorite: !payee.is_favorite })
                        }}
                        className="p-1 rounded hover:bg-accent"
                        title={payee.is_favorite ? t('payees.removeFavorite') : t('payees.addFavorite')}
                        aria-pressed={payee.is_favorite}
                      >
                        <Star
                          size={14}
                          className={payee.is_favorite ? 'fill-amber-400 text-amber-400' : 'text-muted-foreground'}
                        />
                      </button>
                    ) : (
                      <Star
                        size={14}
                        className={payee.is_favorite ? 'fill-amber-400 text-amber-400' : 'text-muted-foreground opacity-50'}
                      />
                    )}
                  </TableCell>
                  <TableCell className="py-2.5 max-w-0 w-full">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-foreground truncate" title={payee.name}>{payee.name}</p>
                      {payee.notes && (
                        <p className="text-xs text-muted-foreground mt-0.5 truncate" title={payee.notes}>{payee.notes}</p>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="hidden md:table-cell py-2.5">
                    {payee.type && (
                      <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded-full capitalize">{typeLabels[payee.type]}</span>
                    )}
                  </TableCell>
                  <TableCell className="py-2.5 text-right">
                    <span className="text-sm tabular-nums text-muted-foreground">{payee.transaction_count}</span>
                  </TableCell>
                  {canWrite && (
                    <TableCell className="py-2.5 pr-4 sm:pr-5">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                          onClick={(e) => { e.stopPropagation(); openEdit(payee) }}
                          title={t('common.edit')}
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                          onClick={(e) => { 
                            e.stopPropagation(); 
                            setPayeesToDelete([payee.id])
                            setDeleteDialogOpen(true)
                          }}
                          disabled={deleteMutation.isPending || bulkDeleteMutation.isPending}
                          title={t('common.delete')}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
              {filtered.length === 0 && (
                <TableRow>
                  <TableCell colSpan={canWrite ? 6 : 4} className="text-center py-16 text-muted-foreground">
                    {t('payees.empty')}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>

          {/* Pagination. Inside the card so the strip reads as part of the
              table rather than as loose controls under it. */}
          {filtered.length > 10 && (
            <div className="px-5 py-3 border-t border-border flex flex-col sm:flex-row items-center justify-between gap-4">
              {totalPages > 1 ? (
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={safePage <= 1}
                    onClick={() => setPage(safePage - 1)}
                  >
                    {t('common.previous')}
                  </Button>
                  <span className="text-sm text-muted-foreground tabular-nums">
                    {safePage} / {totalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={safePage >= totalPages}
                    onClick={() => setPage(safePage + 1)}
                  >
                    {t('common.next')}
                  </Button>
                </div>
              ) : (
                <div className="hidden sm:block" />
              )}

              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">{t('common.rowsPerPage')}</span>
                <Select
                  value={String(pageSize)}
                  onValueChange={(value) => {
                    setPageSize(Number(value))
                    setPage(1)
                    try {
                      localStorage.setItem('securo.payees.pageSize', value)
                    } catch {
                      // ignored
                    }
                  }}
                >
                  <SelectTrigger className="w-[70px] h-8 text-xs">
                    <SelectValue placeholder={pageSize} />
                  </SelectTrigger>
                  <SelectContent>
                    {['10', '20', '50', '100'].map((value) => (
                      <SelectItem key={value} value={value}>{value}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}
          </>
        )}
      </div>

      {/* Payee detail. Edit and Delete open on top of it rather than replacing
          it, so cancelling either lands back on the payee instead of the
          bare table. */}
      <PayeeDetailDialog
        payee={detailPayee}
        canWrite={canWrite}
        onOpenChange={(open) => { if (!open) setSummaryPayee(null) }}
        onEdit={openEdit}
        onDelete={(payee) => {
          setPayeesToDelete([payee.id])
          setDeleteDialogOpen(true)
        }}
      />

      <PayeeFormDialog
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open)
          // The update mutation used to clear this on success. The dialog
          // owns its own closing now, so the page clears on the way out.
          if (!open) setEditingPayee(null)
        }}
        payee={editingPayee}
        onRequestDelete={(payee) => deleteMutation.mutate(payee.id)}
        deletePending={deleteMutation.isPending}
      />

      {/* Merge Dialog */}
      <Dialog open={mergeDialogOpen} onOpenChange={setMergeDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('payees.mergeTitle')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">{t('payees.mergeDescription')}</p>
            <div className="space-y-1">
              {Array.from(selectedIds).map(id => {
                const p = payeesList?.find(x => x.id === id)
                return p ? (
                  <div key={id} className="text-sm py-1 px-2 rounded bg-muted">{p.name} ({p.transaction_count})</div>
                ) : null
              })}
            </div>
            <div className="space-y-2">
              <Label>{t('payees.mergeTarget')}</Label>
              <select
                className="w-full border border-border rounded-md px-3 py-2 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                value={mergeTargetId}
                onChange={(e) => setMergeTargetId(e.target.value)}
              >
                <option value="">{t('payees.selectTarget')}</option>
                {Array.from(selectedIds).map(id => {
                  const p = payeesList?.find(x => x.id === id)
                  return p ? <option key={id} value={id}>{p.name}</option> : null
                })}
              </select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setMergeDialogOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              disabled={!mergeTargetId || mergeMutation.isPending}
              onClick={() => {
                const sourceIds = Array.from(selectedIds).filter(id => id !== mergeTargetId)
                mergeMutation.mutate({ targetId: mergeTargetId, sourceIds })
              }}
            >
              {t('payees.merge')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {payeesToDelete.length > 1 ? t('payees.deleteMultipleTitle') : t('payees.deleteTitle')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {payeesToDelete.length > 1 ? t('payees.deleteMultipleConfirm', { count: payeesToDelete.length }) : t('payees.deleteConfirm')}
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending || bulkDeleteMutation.isPending}
              onClick={() => {
                if (payeesToDelete.length === 1) {
                  deleteMutation.mutate(payeesToDelete[0])
                } else if (payeesToDelete.length > 1) {
                  bulkDeleteMutation.mutate(payeesToDelete)
                }
              }}
            >
              <Trash2 size={14} className="mr-1" />
              {t('common.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
