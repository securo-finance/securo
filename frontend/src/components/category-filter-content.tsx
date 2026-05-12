import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import {
  DropdownMenuCheckboxItem,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
} from '@/components/ui/dropdown-menu'
import type { Category, CategoryGroup } from '@/types'

function toggleInArray(arr: string[], id: string): string[] {
  return arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id]
}

interface CategoryFilterContentProps {
  categoryIds: string[]
  onCategoryIdsChange: (ids: string[]) => void
  filterUncategorized: boolean
  onUncategorizedChange: (value: boolean) => void
  categories: Category[]
  groups: CategoryGroup[]
  onKeepOpen?: () => void
}

export function CategoryFilterContent({
  categoryIds,
  onCategoryIdsChange,
  filterUncategorized,
  onUncategorizedChange,
  categories,
  groups,
  onKeepOpen,
}: CategoryFilterContentProps) {
  const { t } = useTranslation()

  const displayCategoryGroups = useMemo(() => {
    const ungrouped = (categories ?? []).filter((c) => !c.group_id)
    if (ungrouped.length === 0) return groups

    return [
      ...groups,
      {
        id: 'ungrouped-virtual',
        name: t('groups.noGroup'),
        categories: ungrouped,
      } as CategoryGroup,
    ]
  }, [categories, groups, t])

  return (
    <>
      <DropdownMenuCheckboxItem
        checked={filterUncategorized}
        onSelect={(e) => {
          e.preventDefault()
          onKeepOpen?.()
          onUncategorizedChange(!filterUncategorized)
        }}
        className="gap-2 rounded-sm py-1.5 text-[13px]"
      >
        <span className="min-w-0 flex-1 truncate text-left italic text-muted-foreground">
          {t('transactions.uncategorized')}
        </span>
      </DropdownMenuCheckboxItem>
      <div className="my-1 h-px bg-border/60" />
      {displayCategoryGroups.length === 0 ? (
        <div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
          {t('transactions.filtersBar.noOptions')}
        </div>
      ) : (
        displayCategoryGroups.map((group) => (
          <DropdownMenuGroup key={group.id}>
            <DropdownMenuLabel className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
              {group.name}
            </DropdownMenuLabel>
            {group.categories.map((c) => (
              <DropdownMenuCheckboxItem
                key={c.id}
                checked={categoryIds.includes(c.id)}
                onSelect={(e) => {
                  e.preventDefault()
                  onKeepOpen?.()
                  onCategoryIdsChange(toggleInArray(categoryIds, c.id))
                }}
                className="gap-2 rounded-sm py-1.5 text-[13px]"
              >
                {c.color ? (
                  <span
                    className="size-2.5 shrink-0 rounded-full border border-black/5"
                    style={{ backgroundColor: c.color }}
                  />
                ) : null}
                <span className="min-w-0 flex-1 truncate text-left">
                  {c.name}
                </span>
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuGroup>
        ))
      )}
      {(categoryIds.length > 0 || filterUncategorized) && (
        <>
          <div className="my-1 h-px bg-border/60" />
          <DropdownMenuItem
            onSelect={(e) => {
              e.preventDefault()
              onKeepOpen?.()
              onCategoryIdsChange([])
              onUncategorizedChange(false)
            }}
            className="gap-2 rounded-sm px-2 py-1.5 text-[12px] text-muted-foreground"
          >
            <X size={12} />
            {t('transactions.filtersBar.clearSelection')}
          </DropdownMenuItem>
        </>
      )}
    </>
  )
}
