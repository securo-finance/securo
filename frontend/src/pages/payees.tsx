import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { payees as payeesApi, transactions as transactionsApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
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
import { calculateRangeSelection } from '@/lib/selection-utils'
import { Search, Star, Merge, Trash2, ArrowRight, ListFilter, X, Check } from 'lucide-react'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import type { Payee } from '@/types'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

export default function PayeesPage() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const typeLabels: Record<string, string> = {
    merchant: t('payees.typeMerchant'),
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
  const prevSearchRef = useRef<string | null>(null)

  // Sync state from URL when navigating
  useEffect(() => {
    const searchStr = searchParams.toString()
    if (prevSearchRef.current === searchStr) return
    prevSearchRef.current = searchStr

    const nextQ = searchParams.get('q') ?? ''
    setSearch(nextQ)
    setSearchQuery(nextQ)
    setFilterType(searchParams.get('type') ?? '')
    setFilterFavorites(searchParams.get('is_favorite') === 'true')
  }, [searchParams])

  // Sync states back to URL searchParams
  useEffect(() => {
    const params = new URLSearchParams(
      [
        ['q', searchQuery],
        ['type', filterType],
        ['is_favorite', filterFavorites ? 'true' : ''],
      ].filter(([, v]) => v && v.length),
    )

    window.history.replaceState(
      null,
      '',
      params.size ? `?${params}` : window.location.pathname,
    )
  }, [searchQuery, filterType, filterFavorites])

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearchQuery(search)
    }, 300)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [search])

  // Clear selection on filter or search query change
  useEffect(() => {
    setSelectedIds(new Set())
    setLastSelectedId(null)
  }, [searchQuery, filterType, filterFavorites])

  // Form state
  const [formName, setFormName] = useState('')
  const [formType, setFormType] = useState<string>('merchant')
  const [formNotes, setFormNotes] = useState('')

  const { data: payeesList, isLoading } = useQuery({
    queryKey: ['payees', searchQuery, filterType, filterFavorites],
    queryFn: () => payeesApi.list({
      q: searchQuery || undefined,
      type: filterType || undefined,
      is_favorite: filterFavorites || undefined,
    }),
  })

  const { data: summaryData, isLoading: summaryLoading } = useQuery({
    queryKey: ['payees', summaryPayee, 'summary'],
    queryFn: () => payeesApi.summary(summaryPayee!),
    enabled: !!summaryPayee,
  })

  const { data: recentTxData } = useQuery({
    queryKey: ['payees', summaryPayee, 'recent-transactions'],
    queryFn: () => transactionsApi.list({ payee_id: summaryPayee!, limit: 5 }),
    enabled: !!summaryPayee,
  })

  const createMutation = useMutation({
    mutationFn: (data: { name: string; type?: string; notes?: string }) => payeesApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      setDialogOpen(false)
      toast.success(t('payees.created'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Payee> & { id: string }) => payeesApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      setDialogOpen(false)
      setEditingPayee(null)
      toast.success(t('payees.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => payeesApi.delete(id),
    onSuccess: (_, id) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['payees'] })
      setDialogOpen(false)
      setEditingPayee(null)
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

  const favoriteMutation = useMutation({
    mutationFn: ({ id, is_favorite }: { id: string; is_favorite: boolean }) =>
      payeesApi.update(id, { is_favorite }),
    onSuccess: () => {
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

  const openCreate = () => {
    setEditingPayee(null)
    setFormName('')
    setFormType('merchant')
    setFormNotes('')
    setDialogOpen(true)
  }

  const openEdit = (payee: Payee) => {
    setEditingPayee(payee)
    setFormName(payee.name)
    setFormType(payee.type)
    setFormNotes(payee.notes ?? '')
    setDialogOpen(true)
  }

  const handleSave = () => {
    if (editingPayee) {
      updateMutation.mutate({ id: editingPayee.id, name: formName, type: formType as Payee['type'], notes: formNotes || undefined })
    } else {
      createMutation.mutate({ name: formName, type: formType, notes: formNotes || undefined })
    }
  }

  const toggleSelect = (id: string, isShiftKey: boolean = false) => {
    setSelectedIds(prev =>
      calculateRangeSelection(prev, lastSelectedId, id, filtered, isShiftKey)
    )
    setLastSelectedId(id)
  }

  const filtered = payeesList ?? []

  const toggleSelectAll = () => {
    if (!filtered.length) return
    const allSelected = filtered.every(payee => selectedIds.has(payee.id))
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(filtered.map(payee => payee.id)))
    }
  }

  const allSelected = filtered.length > 0 && filtered.every(payee => selectedIds.has(payee.id))
  const someSelected = filtered.some(payee => selectedIds.has(payee.id)) && !allSelected

  return (
    <div>
      <PageHeader
        section={t('payees.section')}
        title={t('payees.title')}
        action={
          canWrite ? (
            <div className="flex items-center gap-2">
              {selectedIds.size >= 2 && (
                <Button variant="outline" onClick={() => { setMergeTargetId(''); setMergeDialogOpen(true) }}>
                  <Merge size={16} className="mr-1.5" />
                  {t('payees.merge')} ({selectedIds.size})
                </Button>
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
                    'inline-flex h-8 items-center gap-1.5 rounded-md border border-border/80 bg-background px-2.5 text-[12px] font-medium text-muted-foreground transition-colors',
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
                        { value: 'merchant', label: t('payees.typeMerchant') },
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
                <TableHead className="text-xs font-medium text-muted-foreground py-3">{t('payees.name')}</TableHead>
                <TableHead className="hidden md:table-cell text-xs font-medium text-muted-foreground py-3 w-[120px]">{t('payees.type')}</TableHead>
                <TableHead className="text-xs font-medium text-muted-foreground py-3 text-right w-[120px]">{t('payees.transactionCount')}</TableHead>
                {canWrite && <TableHead className="w-[60px]" />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((payee) => (
                <TableRow
                  key={payee.id}
                  className={`cursor-pointer hover:bg-muted border-b border-border last:border-0 ${
                    summaryPayee === payee.id ? 'bg-muted/80 font-medium' : selectedIds.has(payee.id) ? 'bg-primary/5' : ''
                  }`}
                  onClick={() => {
                    setSummaryPayee(summaryPayee === payee.id ? null : payee.id)
                  }}
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
                  <TableCell className="py-2.5">
                    <span className="text-sm font-semibold text-foreground">{payee.name}</span>
                    {payee.notes && (
                      <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-[300px]">{payee.notes}</p>
                    )}
                  </TableCell>
                  <TableCell className="hidden md:table-cell py-2.5">
                    <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded-full capitalize">{typeLabels[payee.type] ?? payee.type}</span>
                  </TableCell>
                  <TableCell className="py-2.5 text-right">
                    <span className="text-sm tabular-nums text-muted-foreground">{payee.transaction_count}</span>
                  </TableCell>
                  {canWrite && (
                    <TableCell className="py-2.5 pr-4">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => { e.stopPropagation(); openEdit(payee) }}
                      >
                        {t('common.edit')}
                      </Button>
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
        )}
      </div>

      {/* Summary panel */}
      {summaryPayee && (
        <div className="bg-card rounded-xl border border-border shadow-sm p-5 mb-4">
          {summaryLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : summaryData ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-bold">{summaryData.payee.name}</h3>
                <Button variant="ghost" size="sm" onClick={() => setSummaryPayee(null)}>
                  &times;
                </Button>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">{t('payees.totalSpent')}</p>
                  <p className="text-lg font-bold text-rose-500 tabular-nums">
                    {mask(formatCurrency(summaryData.total_spent, userCurrency, locale))}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{t('payees.totalReceived')}</p>
                  <p className="text-lg font-bold text-emerald-600 tabular-nums">
                    {mask(formatCurrency(summaryData.total_received, userCurrency, locale))}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{t('payees.transactionCount')}</p>
                  <p className="text-lg font-bold tabular-nums">{summaryData.transaction_count}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{t('payees.lastTransaction')}</p>
                  <p className="text-sm font-medium">
                    {summaryData.last_transaction_date
                      ? new Date(summaryData.last_transaction_date + 'T00:00:00').toLocaleDateString(dateLocale)
                      : '—'}
                  </p>
                </div>
              </div>
              {summaryData.most_common_category && (
                <p className="text-xs text-muted-foreground">
                  {t('payees.topCategory')}: <span className="font-medium text-foreground">{summaryData.most_common_category.name}</span>
                </p>
              )}

              {/* Recent transactions */}
              {recentTxData && recentTxData.items.length > 0 && (
                <div className="pt-3 border-t border-border space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">{t('dashboard.recentTransactions')}</p>
                  <div className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                    {recentTxData.items.map((tx) => (
                      <div key={tx.id} className="flex items-center justify-between px-3 py-2 bg-background text-sm">
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-foreground truncate">{tx.description}</p>
                          <p className="text-xs text-muted-foreground">
                            {new Date(tx.date + 'T00:00:00').toLocaleDateString(dateLocale)}
                            {tx.category?.name && <> · {tx.category.name}</>}
                          </p>
                        </div>
                        <span className={`text-sm font-semibold tabular-nums ml-3 ${tx.type === 'debit' ? 'text-rose-500' : 'text-emerald-600'}`}>
                          {mask(formatCurrency(tx.amount, tx.currency, locale))}
                        </span>
                      </div>
                    ))}
                  </div>
                  {recentTxData.total > 5 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="w-full text-xs text-muted-foreground hover:text-foreground gap-1"
                      onClick={() => navigate(`/transactions?payee_id=${summaryPayee}`)}
                    >
                      {t('payees.viewAllTransactions', { count: recentTxData.total })}
                      <ArrowRight size={12} />
                    </Button>
                  )}
                </div>
              )}
            </div>
          ) : null}
        </div>
      )}

      {/* Create/Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editingPayee ? t('payees.edit') : t('payees.add')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>{t('payees.name')}</Label>
              <Input value={formName} onChange={(e) => setFormName(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label>{t('payees.type')}</Label>
              <select
                className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                value={formType}
                onChange={(e) => setFormType(e.target.value)}
              >
                <option value="merchant">{t('payees.typeMerchant')}</option>
                <option value="person">{t('payees.typePerson')}</option>
                <option value="company">{t('payees.typeCompany')}</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label>{t('payees.notes')}</Label>
              <textarea
                className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background resize-none focus:outline-none focus:ring-2 focus:ring-ring"
                rows={2}
                value={formNotes}
                onChange={(e) => setFormNotes(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter className={editingPayee ? 'flex justify-between sm:justify-between' : ''}>
            {editingPayee && (
              <Button
                variant="destructive"
                onClick={() => deleteMutation.mutate(editingPayee.id)}
                disabled={deleteMutation.isPending}
              >
                <Trash2 size={14} className="mr-1" />
                {t('common.delete')}
              </Button>
            )}
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>
                {t('common.cancel')}
              </Button>
              <Button
                onClick={handleSave}
                disabled={!formName.trim() || createMutation.isPending || updateMutation.isPending}
              >
                {t('common.save')}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
                className="w-full border border-border rounded-md px-3 py-2 text-sm bg-background focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
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
    </div>
  )
}
