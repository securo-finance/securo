import { useState, useMemo, useContext } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ChevronDownIcon, CheckIcon, PlusIcon } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem } from '@/components/ui/command'
import { WorkspaceContext } from '@/contexts/workspace-context'
import { categories as categoriesApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategoryQueries } from '@/lib/invalidate-queries'
import { inlineCreateItemValue, inlineCreateName } from '@/lib/inline-create'
import type { Category, CategoryGroup } from '@/types'
import { cn, normalizeText } from '@/lib/utils'
import {
  isCategoryHiddenFromSelection,
  resolveSelectedCategory,
} from '@/lib/category-selection-utils'

// Same starting look the categories page gives a new category, so one made
// from a picker is indistinguishable from one made there.
const NEW_CATEGORY_ICON = 'circle-help'
const NEW_CATEGORY_COLOR = '#6366f1'

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
  const [search, setSearch] = useState('')
  // The category just created here. The list catches up once the refetch
  // lands; until then this keeps the trigger showing the new name.
  const [created, setCreated] = useState<Category | null>(null)
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  // Read the context directly rather than through useWorkspace, so the
  // picker still renders (without creation) outside a workspace provider.
  const canWrite = useContext(WorkspaceContext)?.canWrite ?? false
  const canCreate = creatable && canWrite

  const createMutation = useMutation({
    mutationFn: (name: string) =>
      categoriesApi.create({ name, icon: NEW_CATEGORY_ICON, color: NEW_CATEGORY_COLOR }),
    onSuccess: (category) => {
      // Only the category lists refetch. Whatever form hosts this picker
      // (an import preview, a half-filled transaction) keeps its state.
      invalidateCategoryQueries(queryClient)
      setCreated(category)
      onChange(category.id)
      handleOpenChange(false)
      toast.success(t('categories.created'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) setSearch('')
  }

  const createName = canCreate
    ? inlineCreateName(search, (categories ?? []).map((c) => c.name))
    : null

  const resolvedPlaceholder = placeholder ?? t('transactions.selectCategory', 'Select category')

  const displayGroups = useMemo(() => {
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
        <Command
          filter={(itemValue, search) => {
            return normalizeText(itemValue).includes(normalizeText(search)) ? 1 : 0
          }}
        >
          <CommandInput
            placeholder={t('transactions.searchCategory')}
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>{t('transactions.noCategoryFound')}</CommandEmpty>
            {allowNone && (
              <CommandGroup>
                <CommandItem
                  value={`none ${t('transactions.noCategory')}`}
                  onSelect={() => {
                    onChange('')
                    handleOpenChange(false)
                  }}
                  className="italic text-muted-foreground cursor-pointer"
                >
                  <span className="flex-1">{t('transactions.noCategory')}</span>
                  {value === '' && <CheckIcon className="size-4 shrink-0" />}
                </CommandItem>
              </CommandGroup>
            )}
            {displayGroups.map((group) => (
              <CommandGroup key={group.id}>
                <div className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
                  {group.name}
                </div>
                {group.categories.map((cat) => (
                  <CommandItem
                    key={cat.id}
                    value={`${group.name} ${cat.name}`}
                    onSelect={() => {
                      onChange(cat.id)
                      handleOpenChange(false)
                    }}
                    className="cursor-pointer"
                  >
                    <div className="flex items-center gap-2 min-w-0 truncate flex-1">
                      {cat.color ? (
                        <span
                          className="size-2.5 shrink-0 rounded-full border border-black/5"
                          style={{ backgroundColor: cat.color }}
                        />
                      ) : null}
                      <span className="truncate">{cat.name}</span>
                    </div>
                    {value === cat.id && <CheckIcon className="size-4 shrink-0" />}
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
            {createName && (
              <CommandGroup>
                <CommandItem
                  value={inlineCreateItemValue(search)}
                  disabled={createMutation.isPending}
                  onSelect={() => createMutation.mutate(createName)}
                  className="cursor-pointer"
                >
                  <PlusIcon className="size-4 shrink-0 text-muted-foreground" />
                  <span className="truncate">{t('common.createNamed', { name: createName })}</span>
                </CommandItem>
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
