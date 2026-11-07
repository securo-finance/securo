import { useMemo, useRef, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChartPie } from 'lucide-react'
import type { ImportReviewTransaction, Category, CategoryGroup } from '@/types'
import { formatCurrency } from '@/lib/format'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Input } from '@/components/ui/input'
import { CategorySelect } from '@/components/category-select'
import { CategoryFilterDropdown } from '@/components/category-filter-dropdown'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const ISO_DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/

function formatLocalDate(date: string, locale: string) {
  const match = ISO_DATE_RE.exec(date)
  if (!match) return new Date(date).toLocaleDateString(locale)
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])).toLocaleDateString(locale)
}

interface ImportReviewTableProps {
  transactions: ImportReviewTransaction[]
  categories: Category[]
  groups: CategoryGroup[]
  userCurrency: string
  locale: string
  dateLocale: string
  searchQuery: string
  filterCategoryIds: string[]
  filterUncategorized: boolean
  statusFilter: 'all' | 'included' | 'excluded'
  currentPage: number
  onToggleExcluded: (id: string) => void
  onToggleAllFiltered: (ids: string[], excluded: boolean) => void
  onChangeCategory: (id: string, categoryId: string | null) => void
  onSearchChange: (query: string) => void
  onCategoryIdsChange: (ids: string[]) => void
  onUncategorizedChange: (value: boolean) => void
  onStatusFilterChange: (filter: 'all' | 'included' | 'excluded') => void
  onPageChange: (page: number) => void
}

export function ImportReviewTable({
  transactions,
  categories,
  groups,
  userCurrency,
  locale,
  dateLocale,
  searchQuery,
  filterCategoryIds,
  filterUncategorized,
  statusFilter,
  currentPage,
  onToggleExcluded,
  onToggleAllFiltered,
  onChangeCategory,
  onSearchChange,
  onCategoryIdsChange,
  onUncategorizedChange,
  onStatusFilterChange,
  onPageChange,
}: ImportReviewTableProps) {
  const { t } = useTranslation()
  const [pageSize, setPageSize] = useState<number>(() => {
    try {
      const stored = localStorage.getItem('securo.import.pageSize')
      return stored ? Number(stored) : 50
    } catch {
      return 50
    }
  })

  const selectAllRef = useRef<HTMLInputElement>(null)
  const hasCategoryFilter = filterCategoryIds.length > 0 || filterUncategorized

  // A split read back from an export names its categories; resolve them the
  // way the import will (by name, case-insensitively). A name missing here,
  // or shared by two categories, cannot be resolved and the row imports
  // without its split.
  const categoryIdsByName = useMemo(() => {
    const byName = new Map<string, string[]>()
    for (const c of categories) {
      const key = c.name.trim().toLowerCase()
      byName.set(key, [...(byName.get(key) ?? []), c.id])
    }
    return byName
  }, [categories])
  const splitPartIds = (tx: ImportReviewTransaction): string[] =>
    (tx.category_lines ?? []).flatMap((line) => {
      const ids = categoryIdsByName.get(line.category_name.trim().toLowerCase()) ?? []
      return ids.length === 1 ? ids : []
    })
  // Part names that match no category here, or more than one: either way the
  // row imports without its split, but the fix differs (create vs rename).
  const unresolvedParts = (tx: ImportReviewTransaction, ambiguous: boolean): string[] =>
    (tx.category_lines ?? [])
      .filter((line) => {
        const matches = (categoryIdsByName.get(line.category_name.trim().toLowerCase()) ?? []).length
        return ambiguous ? matches > 1 : matches === 0
      })
      .map((line) => line.category_name)

  // Two parts that resolve to the same category: a split holds each once.
  const repeatsCategory = (tx: ImportReviewTransaction): boolean => {
    const ids = splitPartIds(tx)
    return new Set(ids).size !== ids.length
  }

  const filtered = useMemo(() => {
    return transactions.filter(tx => {
      if (searchQuery) {
        const q = searchQuery.toLowerCase()
        if (!tx.description.toLowerCase().includes(q)) return false
      }
      if (hasCategoryFilter && tx.category_lines?.length) {
        // A split row is categorized; a category filter matches any part.
        const partIds = splitPartIds(tx)
        if (!filterCategoryIds.some((id) => partIds.includes(id))) return false
      } else if (hasCategoryFilter) {
        const catId = tx.selected_category_id !== undefined ? tx.selected_category_id : tx.suggested_category_id
        if (filterUncategorized && !filterCategoryIds.length) {
          if (catId) return false
        } else if (filterUncategorized) {
          if (catId && !filterCategoryIds.includes(catId)) return false
        } else {
          if (!catId || !filterCategoryIds.includes(catId)) return false
        }
      }
      if (statusFilter === 'included' && tx.excluded) return false
      if (statusFilter === 'excluded' && !tx.excluded) return false
      return true
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transactions, searchQuery, filterCategoryIds, filterUncategorized, hasCategoryFilter, statusFilter, categoryIdsByName])

  const allFilteredIncluded = filtered.length > 0 && filtered.every(tx => !tx.excluded)
  const someFilteredIncluded = filtered.some(tx => !tx.excluded)

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someFilteredIncluded && !allFilteredIncluded
    }
  }, [allFilteredIncluded, someFilteredIncluded])

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const safePage = Math.min(currentPage, totalPages)
  const pageItems = filtered.slice((safePage - 1) * pageSize, safePage * pageSize)

  return (
    <div>
      {/* Filter bar */}
      <div className="px-5 py-3 border-b border-border bg-muted/30 flex flex-wrap items-center gap-3">
        <Input
          placeholder={t('import.searchTransactions')}
          value={searchQuery}
          onChange={(e) => { onSearchChange(e.target.value); onPageChange(1) }}
          className="max-w-xs h-8 text-sm border border-border rounded-md px-3 bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
        />
        <CategoryFilterDropdown
          categoryIds={filterCategoryIds}
          onCategoryIdsChange={(ids) => { onCategoryIdsChange(ids); onPageChange(1) }}
          filterUncategorized={filterUncategorized}
          onUncategorizedChange={(v) => { onUncategorizedChange(v); onPageChange(1) }}
          categories={categories}
          groups={groups}
          label={t('import.filterCategory')}
        />
        <select
          className="border border-border rounded-md px-3 py-1.5 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
          value={statusFilter}
          onChange={(e) => { onStatusFilterChange(e.target.value as 'all' | 'included' | 'excluded'); onPageChange(1) }}
        >
          <option value="all">{t('import.allStatus')}</option>
          <option value="included">{t('import.included')}</option>
          <option value="excluded">{t('import.excluded')}</option>
        </select>
      </div>

      {/* Table */}
      <div className="max-h-[480px] overflow-auto">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent bg-transparent border-b border-border">
              <TableHead className="text-xs font-medium text-muted-foreground py-3 pl-4 w-[40px]">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  checked={allFilteredIncluded}
                  onChange={() => {
                    const ids = filtered.map(tx => tx._id)
                    onToggleAllFiltered(ids, allFilteredIncluded)
                  }}
                  disabled={filtered.length === 0}
                  title={t('import.selectAll')}
                  className="rounded border-border text-primary focus:ring-primary"
                />
              </TableHead>
              <TableHead className="text-xs font-medium text-muted-foreground py-3 w-[100px]">
                {t('transactions.date')}
              </TableHead>
              <TableHead className="text-xs font-medium text-muted-foreground py-3">
                {t('transactions.description')}
              </TableHead>
              <TableHead className="text-xs font-medium text-muted-foreground py-3 text-right w-[120px]">
                {t('transactions.amount')}
              </TableHead>
              <TableHead className="text-xs font-medium text-muted-foreground py-3 w-[160px]">
                {t('import.category')}
              </TableHead>
              <TableHead className="text-xs font-medium text-muted-foreground py-3 pr-4 w-[90px]">
                {t('transactions.status')}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {pageItems.map((tx) => {
              return (
                <TableRow
                  key={tx._id}
                  className={`border-b border-border last:border-0 hover:bg-muted ${tx.excluded ? 'opacity-50' : ''}`}
                >
                  <TableCell className="py-2.5 pl-4">
                    <input
                      type="checkbox"
                      checked={!tx.excluded}
                      onChange={() => onToggleExcluded(tx._id)}
                      className="rounded border-border text-primary focus:ring-primary"
                    />
                  </TableCell>
                  <TableCell className="py-2.5 text-xs text-muted-foreground whitespace-nowrap">
                    {formatLocalDate(tx.date, dateLocale)}
                  </TableCell>
                  <TableCell className={`py-2.5 text-sm ${tx.excluded ? 'line-through text-muted-foreground' : 'text-foreground'}`}>
                    {tx.description}
                  </TableCell>
                  <TableCell className={`py-2.5 text-right text-sm font-bold tabular-nums ${tx.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
                    {tx.type === 'credit' ? '+' : '−'}{formatCurrency(Math.abs(Number(tx.amount)), userCurrency, locale)}
                  </TableCell>
                  <TableCell className="py-2.5">
                    {tx.category_lines?.length ? (
                      // A split from an export comes back as it was; the
                      // parts are listed on hover.
                      <span className="flex flex-col gap-1">
                        <span
                          title={tx.category_lines
                            .map((line) => `${line.category_name}: ${formatCurrency(Number(line.amount), tx.currency ?? userCurrency, locale)}`)
                            .join('\n')}
                          className="inline-flex w-fit items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-violet-700 bg-violet-50 border border-violet-200 dark:bg-violet-950/40 dark:text-violet-300 dark:border-violet-900 px-1.5 py-0.5 rounded-full"
                        >
                          <ChartPie size={10} />
                          {t('categoryLines.badge', { count: tx.category_lines.length })}
                        </span>
                        {unresolvedParts(tx, false).length > 0 && (
                          <span className="text-[11px] text-amber-600 dark:text-amber-400">
                            {t('categoryLines.importUnresolved', { names: unresolvedParts(tx, false).join(', ') })}
                          </span>
                        )}
                        {unresolvedParts(tx, true).length > 0 && (
                          <span className="text-[11px] text-amber-600 dark:text-amber-400">
                            {t('categoryLines.importAmbiguous', { names: unresolvedParts(tx, true).join(', ') })}
                          </span>
                        )}
                        {repeatsCategory(tx) && (
                          <span className="text-[11px] text-amber-600 dark:text-amber-400">
                            {t('categoryLines.errors.duplicateCategory')}
                          </span>
                        )}
                      </span>
                    ) : (
                    <CategorySelect
                      value={tx.selected_category_id !== undefined
                        ? (tx.selected_category_id ?? '')
                        : (tx.suggested_category_id ?? '')}
                      onChange={(v) => onChangeCategory(tx._id, v || null)}
                      categories={categories}
                      groups={groups}
                      placeholder={t('import.noCategory')}
                      allowNone
                      creatable
                      className="w-full border border-border rounded-md px-2 py-1 text-xs bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                    />
                    )}
                  </TableCell>
                  <TableCell className="py-2.5 pr-4">
                    {tx.excluded ? (
                      <span className="text-xs bg-muted text-muted-foreground px-2 py-0.5 rounded">
                        {t('import.excluded')}
                      </span>
                    ) : (
                      <span className="text-xs bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded">
                        {t('import.included')}
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      {filtered.length > 10 && (
        <div className="px-5 py-3 border-t border-border flex flex-col sm:flex-row items-center justify-between gap-4 text-sm">
          {totalPages > 1 ? (
            <div className="flex items-center gap-4">
              <button
                className="text-muted-foreground hover:text-foreground disabled:opacity-30 font-medium"
                disabled={safePage <= 1}
                onClick={() => onPageChange(safePage - 1)}
              >
                ← {t('common.previous', 'Previous')}
              </button>
              <span className="text-xs text-muted-foreground">
                {t('import.page', { current: safePage, total: totalPages })}
              </span>
              <button
                className="text-muted-foreground hover:text-foreground disabled:opacity-30 font-medium"
                disabled={safePage >= totalPages}
                onClick={() => onPageChange(safePage + 1)}
              >
                {t('common.next', 'Next')} →
              </button>
            </div>
          ) : (
            <div className="hidden sm:block" />
          )}

          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">{t('common.rowsPerPage', 'Rows per page')}</span>
            <Select
              value={String(pageSize)}
              onValueChange={(val) => {
                const nextSize = Number(val)
                setPageSize(nextSize)
                onPageChange(1)
                try {
                  localStorage.setItem('securo.import.pageSize', String(nextSize))
                } catch {
                  // ignored
                }
              }}
            >
              <SelectTrigger className="w-[70px] h-8 text-xs">
                <SelectValue placeholder={pageSize} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
                <SelectItem value="100">100</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      )}
    </div>
  )
}
