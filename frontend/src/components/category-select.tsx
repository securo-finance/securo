import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { Category } from '@/types'

interface CategorySelectProps {
  value: string
  onChange: (value: string) => void
  categories: Category[]
  placeholder?: string
  disabled?: boolean
  className?: string
  uncategorizedLabel?: string
  contentProps?: React.ComponentProps<typeof SelectContent>
}

export function CategorySelect({
  value,
  onChange,
  categories,
  placeholder = 'Select category',
  disabled = false,
  className = "w-full border border-border rounded-md px-3 py-2 text-sm bg-background disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]",
  uncategorizedLabel,
  contentProps,
}: CategorySelectProps) {
  return (
    <Select
      value={value === '' ? 'uncategorized-internal-value' : value}
      onValueChange={(nextValue) => {
        onChange(nextValue === 'uncategorized-internal-value' ? '' : nextValue)
      }}
      disabled={disabled}
    >
      <SelectTrigger className={className}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent position="popper" {...contentProps}>
        {uncategorizedLabel && (
          <SelectItem value="uncategorized-internal-value" className="italic text-muted-foreground">
            {uncategorizedLabel}
          </SelectItem>
        )}
        {categories.map((cat) => (
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
      </SelectContent>
    </Select>
  )
}
