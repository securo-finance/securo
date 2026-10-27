import { useState, useMemo, useContext } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDownIcon } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { CategoryPicker } from '@/components/category-picker'
import { WorkspaceContext } from '@/contexts/workspace-context'
import type { Category, CategoryGroup } from '@/types'
import { cn } from '@/lib/utils'
import {
  isCategoryHiddenFromSelection,
  resolveSelectedCategory,
} from '@/lib/category-selection-utils'

interface CategorySelectProps {
  value: string
  onChange: (value: string) => void
  categories: Category[]
  groups: CategoryGroup[]
  currentCategory?: Category | null
  placeholder?: string
  disabled?: boolean
  className?: string
  allowNone?: boolean
  /**
   * Offer to create a category named after the search text when nothing
   * matches it. Only shown to members who can write to the workspace.
   */
  creatable?: boolean
  contentProps?: React.ComponentProps<typeof PopoverContent>
}


export function CategorySelect({
  value,
  onChange,
  categories,
  groups,
  currentCategory,
  placeholder,
  disabled = false,
  className,
  allowNone = false,
  creatable = false,
  contentProps,
}: CategorySelectProps) {
  const [open, setOpen] = useState(false)
  // The category just created here. The list catches up once the refetch
  // lands; until then this keeps the trigger showing the new name.
  const [created, setCreated] = useState<Category | null>(null)
  const { t } = useTranslation()
  // Read the context directly rather than through useWorkspace, so the
  // picker still renders (without creation) outside a workspace provider.
  const canWrite = useContext(WorkspaceContext)?.canWrite ?? false
  const canCreate = creatable && canWrite

  function handleOpenChange(next: boolean) {
    setOpen(next)
  }

  const resolvedPlaceholder = placeholder ?? t('transactions.selectCategory', 'Select category')

  const selectedCategory = useMemo(() => {
    const fallback = created && created.id === value ? created : currentCategory
    return resolveSelectedCategory(categories ?? [], value, fallback)
  }, [categories, created, currentCategory, value])
  // A category created a moment ago is not hidden, only not refetched yet.
  const selectedCategoryIsHidden =
    selectedCategory !== created
    && isCategoryHiddenFromSelection(categories ?? [], selectedCategory)

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded-md border border-input bg-card px-3 py-2 text-sm text-left shadow-xs transition-[color,box-shadow] outline-hidden focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50 h-9 cursor-pointer",
            className
          )}
        >
          <span className="flex items-center gap-2 min-w-0 truncate">
            {selectedCategory ? (
              <>
                {selectedCategory.color ? (
                  <span
                    className="size-2.5 shrink-0 rounded-full border border-black/5"
                    style={{ backgroundColor: selectedCategory.color }}
                  />
                ) : null}
                <span className="truncate">{selectedCategory.name}</span>
                {selectedCategoryIsHidden && (
                  <span className="shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                    {t('categories.hiddenBadge')}
                  </span>
                )}
              </>
            ) : value === '' && allowNone ? (
              <span className="italic text-muted-foreground truncate">{t('transactions.noCategory')}</span>
            ) : (
              <span className="text-muted-foreground truncate">{resolvedPlaceholder}</span>
            )}
          </span>
          <ChevronDownIcon className="size-4 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] p-0 overflow-hidden"
        {...contentProps}
      >
        <CategoryPicker
          value={value}
          onChange={(next) => {
            onChange(next)
            handleOpenChange(false)
          }}
          categories={categories}
          groups={groups}
          allowNone={allowNone}
          creatable={canCreate}
          onCreated={setCreated}
        />
      </PopoverContent>
    </Popover>
  )
}
