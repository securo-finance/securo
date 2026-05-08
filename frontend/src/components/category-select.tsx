import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { Category, CategoryGroup } from '@/types'

interface CategorySelectProps {
  value: string
  onChange: (value: string) => void
  categories: Category[]
  groups: CategoryGroup[]
  placeholder?: string
  disabled?: boolean
  className?: string
  allowNone?: boolean
  contentProps?: React.ComponentProps<typeof SelectContent>
}

export function CategorySelect({
  value,
  onChange,
  categories,
  groups,
  placeholder = 'Select category',
  disabled = false,
  className = "w-full border border-border rounded-md px-3 py-2 text-sm bg-background disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]",
  allowNone = false,
  contentProps,
}: CategorySelectProps) {
  const { t } = useTranslation()

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
    <Select
      value={value === '' ? (allowNone ? 'none' : undefined) : value}
      onValueChange={(nextValue) => {
        onChange(nextValue === 'none' ? '' : nextValue)
      }}
      disabled={disabled}
    >
      <SelectTrigger className={className}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent position="popper" {...contentProps}>
        {allowNone && (
          <SelectItem value="none" className="italic text-muted-foreground">
            {t('transactions.noCategory')}
          </SelectItem>
        )}
        {displayGroups.map((group) => (
          <SelectGroup key={group.id}>
            <SelectLabel className="px-2 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
              {group.name}
            </SelectLabel>
            {group.categories.map((cat) => (
              <SelectItem key={cat.id} value={cat.id}>
                {cat.color ? (
                  <span
                    className="size-2.5 shrink-0 rounded-full border border-black/5"
                    style={{ backgroundColor: cat.color }}
                  />
                ) : null}
                <span>{cat.name}</span>
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  )
}
