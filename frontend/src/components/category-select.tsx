import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDownIcon, CheckIcon } from 'lucide-react'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem } from '@/components/ui/command'
import type { Category, CategoryGroup } from '@/types'
import { cn, normalizeText } from '@/lib/utils'

interface CategorySelectProps {
  value: string
  onChange: (value: string) => void
  categories: Category[]
  groups: CategoryGroup[]
  placeholder?: string
  disabled?: boolean
  className?: string
  allowNone?: boolean
  contentProps?: React.ComponentProps<typeof PopoverContent>
}


export function CategorySelect({
  value,
  onChange,
  categories,
  groups,
  placeholder,
  disabled = false,
  className,
  allowNone = false,
  contentProps,
}: CategorySelectProps) {
  const [open, setOpen] = useState(false)
  const { t } = useTranslation()

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
    return (categories ?? []).find((c) => c.id === value)
  }, [categories, value])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded-md border border-input bg-transparent px-3 py-2 text-sm text-left shadow-xs transition-[color,box-shadow] outline-hidden focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50 h-9 cursor-pointer",
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
          <CommandInput placeholder={t('transactions.searchCategory')} />
          <CommandList>
            <CommandEmpty>{t('transactions.noCategoryFound')}</CommandEmpty>
            {allowNone && (
              <CommandGroup>
                <CommandItem
                  value={`none ${t('transactions.noCategory')}`}
                  onSelect={() => {
                    onChange('')
                    setOpen(false)
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
                      setOpen(false)
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
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
