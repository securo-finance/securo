import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Tag, ChevronDown } from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { CategoryFilterContent } from '@/components/category-filter-content'
import type { Category, CategoryGroup } from '@/types'

interface CategoryFilterDropdownProps {
  categoryIds: string[]
  onCategoryIdsChange: (ids: string[]) => void
  filterUncategorized: boolean
  onUncategorizedChange: (value: boolean) => void
  categories: Category[]
  groups: CategoryGroup[]
  label?: string
  triggerClassName?: string
}

export function CategoryFilterDropdown({
  categoryIds,
  onCategoryIdsChange,
  filterUncategorized,
  onUncategorizedChange,
  categories,
  groups,
  label,
  triggerClassName,
}: CategoryFilterDropdownProps) {
  const { t } = useTranslation()

  const categoryById = useMemo(() => {
    const map = new Map<string, Category>()
    categories.forEach((c) => map.set(c.id, c))
    return map
  }, [categories])

  const summary = useMemo(() => {
    const total = categoryIds.length + (filterUncategorized ? 1 : 0)
    if (total > 1) return t('transactions.filtersBar.nSelected', { count: total })
    if (filterUncategorized) return t('transactions.uncategorized')
    if (categoryIds.length === 1) return categoryById.get(categoryIds[0])?.name ?? ''
    return ''
  }, [categoryIds, filterUncategorized, categoryById, t])

  const displayLabel = label ?? t('transactions.category')
  const hasFilter = categoryIds.length > 0 || filterUncategorized

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={
            triggerClassName ??
            `inline-flex min-w-[10rem] h-8 items-center gap-1.5 justify-between rounded-md border border-border px-3 text-sm bg-background hover:bg-muted transition-colors focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]`
          }
        >
          <Tag size={13} className="text-muted-foreground" />
          <span className={`flex-1 text-left ${hasFilter ? 'text-foreground' : 'text-muted-foreground'}`}>
            {summary || displayLabel}
          </span>
          <ChevronDown size={12} className="text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-[320px] w-[240px] overflow-y-auto p-1">
        <CategoryFilterContent
          categoryIds={categoryIds}
          onCategoryIdsChange={onCategoryIdsChange}
          filterUncategorized={filterUncategorized}
          onUncategorizedChange={onUncategorizedChange}
          categories={categories}
          groups={groups}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
