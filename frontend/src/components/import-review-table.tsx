import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
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

const PAGE_SIZE = 50

interface ImportReviewTableProps {
  transactions: ImportReviewTransaction[]
  categories: Category[]
  groups: CategoryGroup[]
  userCurrency: string
  locale: string
  searchQuery: string
  filterCategoryIds: string[]
  filterUncategorized: boolean
  statusFilter: 'all' | 'included' | 'excluded'
  currentPage: number
  onToggleExcluded: (id: string) => void
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
  searchQuery,
  filterCategoryIds,
  filterUncategorized,
  statusFilter,
  currentPage,
  onToggleExcluded,
  onChangeCategory,
  onSearchChange,
  onCategoryIdsChange,
  onUncategorizedChange,
  onStatusFilterChange,
  onPageChange,
}: ImportReviewTableProps) {
  const { t } = useTranslation()

  const hasCategoryFilter = filterCategoryIds.length > 0 || filterUncategorized

  const filtered = useMemo(() => {
    return transactions.filter(tx => {
      if (searchQuery) {
        const q = searchQuery.toLowerCase()
        if (!tx.description.toLowerCase().includes(q)) return false
      }
      if (hasCategoryFilter) {
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
  }, [transactions, searchQuery, filterCategoryIds, filterUncategorized, hasCategoryFilter, statusFilter])

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage = Math.min(currentPage, totalPages)
  const pageItems = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  return (
    <div>
      {/* Filter bar */}
      <div className="px-5 py-3 border-b border-border bg-muted/30 flex flex-wrap items-center gap-3">
        <Input
          placeholder={t('import.searchTransactions')}
          value={searchQuery}
          onChange={(e) => { onSearchChange(e.target.value); onPageChange(1) }}
          className="max-w-xs h-8 text-sm border border-border rounded-md px-3 bg-background focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
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
          className="border border-border rounded-md px-3 py-1.5 text-sm bg-background focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
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
                <span className="sr-only">Toggle</span>
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
                    {new Date(tx.date).toLocaleDateString(locale)}
                  </TableCell>
                  <TableCell className={`py-2.5 text-sm ${tx.excluded ? 'line-through text-muted-foreground' : 'text-foreground'}`}>
                    {tx.description}
                  </TableCell>
                  <TableCell className={`py-2.5 text-right text-sm font-bold tabular-nums ${tx.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
                    {tx.type === 'credit' ? '+' : '−'}{formatCurrency(Math.abs(Number(tx.amount)), userCurrency, locale)}
                  </TableCell>
                  <TableCell className="py-2.5">
                    <CategorySelect
                      value={tx.selected_category_id !== undefined
                        ? (tx.selected_category_id ?? '')
                        : (tx.suggested_category_id ?? '')}
                      onChange={(v) => onChangeCategory(tx._id, v || null)}
                      categories={categories}
                      groups={groups}
                      placeholder={t('import.noCategory')}
                      allowNone
                      className="w-full border border-border rounded-md px-2 py-1 text-xs bg-background focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]"
                    />
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
      {totalPages > 1 && (
        <div className="px-5 py-3 border-t border-border flex items-center justify-between text-sm">
          <button
            className="text-muted-foreground hover:text-foreground disabled:opacity-30"
            disabled={safePage <= 1}
            onClick={() => onPageChange(safePage - 1)}
          >
            ← {t('common.previous', 'Previous')}
          </button>
          <span className="text-xs text-muted-foreground">
            {t('import.page', { current: safePage, total: totalPages })}
          </span>
          <button
            className="text-muted-foreground hover:text-foreground disabled:opacity-30"
            disabled={safePage >= totalPages}
            onClick={() => onPageChange(safePage + 1)}
          >
            {t('common.next', 'Next')} →
          </button>
        </div>
      )}
    </div>
  )
}
