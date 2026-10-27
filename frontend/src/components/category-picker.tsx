import { useEffect, useRef, useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { CheckIcon, PlusIcon } from 'lucide-react'
import { Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem } from '@/components/ui/command'
import { categories as categoriesApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateCategoryQueries } from '@/lib/invalidate-queries'
import { inlineCreateItemValue, inlineCreateName } from '@/lib/inline-create'
import type { Category, CategoryGroup } from '@/types'
import { normalizeText } from '@/lib/utils'

// Same starting look the categories page gives a new category, so one made
// from a picker is indistinguishable from one made there.
const NEW_CATEGORY_ICON = 'circle-help'
const NEW_CATEGORY_COLOR = '#6366f1'

interface CategoryPickerProps {
  /** The current selection; `null` when there is none to show (e.g. a bulk action). */
  value: string | null
  onChange: (value: string) => void
  categories: Category[]
  groups: CategoryGroup[]
  allowNone?: boolean
  /** Offer to create a category named after the search text when nothing matches it. */
  creatable?: boolean
  /** Called with a category just created from the search text, before `onChange`. */
  onCreated?: (category: Category) => void
  /** Focus the search field once mounted, for hosts that don't do it themselves. */
  autoFocus?: boolean
}

/**
 * The searchable, grouped category list. Hosts (the select's popover, the
 * transactions context menu) provide the surface around it.
 */
export function CategoryPicker({
  value,
  onChange,
  categories,
  groups,
  allowNone = false,
  creatable = false,
  onCreated,
  autoFocus = false,
}: CategoryPickerProps) {
  const [search, setSearch] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: (name: string) =>
      categoriesApi.create({ name, icon: NEW_CATEGORY_ICON, color: NEW_CATEGORY_COLOR }),
    onSuccess: (category) => {
      // Only the category lists refetch. Whatever form hosts this picker
      // (an import preview, a half-filled transaction) keeps its state.
      invalidateCategoryQueries(queryClient)
      onCreated?.(category)
      onChange(category.id)
      toast.success(t('categories.created'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  // Deferred a tick: a Radix menu keeps focus on its own trigger while the
  // pointer is still over it, and would take it back from an eager autoFocus.
  useEffect(() => {
    if (!autoFocus) return
    const id = setTimeout(() => rootRef.current?.querySelector('input')?.focus(), 50)
    return () => clearTimeout(id)
  }, [autoFocus])

  const createName = creatable
    ? inlineCreateName(search, (categories ?? []).map((c) => c.name))
    : null

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

  return (
    <Command
      ref={rootRef}
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
  )
}
