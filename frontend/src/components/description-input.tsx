import { useEffect, useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Input } from '@/components/ui/input'
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover'
import { transactions as transactionsApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { DescriptionSuggestion } from '@/types'

const MIN_QUERY = 2
const DEBOUNCE_MS = 200

interface DescriptionInputProps {
  value: string
  onChange: (value: string) => void
  /** Called when the user picks a suggestion, after `onChange`. */
  onPick?: (suggestion: DescriptionSuggestion) => void
  required?: boolean
  className?: string
}

/**
 * Free-text description field that offers descriptions used before in the
 * workspace (issue #1127). Suggestions are a shortcut only: whatever the user
 * types is kept as typed.
 */
export function DescriptionInput({ value, onChange, onPick, required, className }: DescriptionInputProps) {
  const listId = useId()
  const [focused, setFocused] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [active, setActive] = useState(-1)
  const [query, setQuery] = useState(value.trim())

  useEffect(() => {
    const id = setTimeout(() => setQuery(value.trim()), DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [value])

  const { data: suggestions = [] } = useQuery({
    queryKey: ['transactions', 'description-suggestions', query],
    queryFn: () => transactionsApi.descriptionSuggestions(query),
    enabled: focused && query.length >= MIN_QUERY,
    staleTime: 30_000,
  })
  // Offering exactly what's already typed is noise.
  const options = suggestions.filter((s) => s.description !== value.trim())
  // Until the debounce catches up, the results belong to an older query:
  // don't offer "coffee" suggestions under "market".
  const settled = query === value.trim()
  const open = focused && !dismissed && settled && value.trim().length >= MIN_QUERY && options.length > 0

  function pick(suggestion: DescriptionSuggestion) {
    onChange(suggestion.description)
    onPick?.(suggestion)
    setDismissed(true)
    setActive(-1)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => (i + 1) % options.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => (i <= 0 ? options.length - 1 : i - 1))
    } else if (e.key === 'Enter' && active >= 0 && active < options.length) {
      // Pick instead of submitting the form; Enter with nothing highlighted
      // still saves, as it does on a plain input.
      e.preventDefault()
      pick(options[active])
    } else if (e.key === 'Escape') {
      // Close the list without closing the surrounding dialog.
      e.preventDefault()
      e.stopPropagation()
      setDismissed(true)
    }
  }

  return (
    <Popover open={open}>
      <PopoverAnchor asChild>
        <Input
          value={value}
          onChange={(e) => {
            onChange(e.target.value)
            setDismissed(false)
            setActive(-1)
          }}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onKeyDown={handleKeyDown}
          required={required}
          autoComplete="off"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
          className={className}
        />
      </PopoverAnchor>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] p-1"
        // Keep focus (and typing) in the input.
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        <ul id={listId} role="listbox" className="max-h-60 overflow-y-auto">
          {options.map((s, i) => (
            <li
              key={s.description}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              // mousedown, not click: the input's blur would close the list first.
              onMouseDown={(e) => {
                e.preventDefault()
                pick(s)
              }}
              onMouseEnter={() => setActive(i)}
              className={cn(
                'cursor-pointer truncate rounded-md px-2 py-1.5 text-sm',
                i === active && 'bg-accent text-accent-foreground',
              )}
            >
              {s.description}
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  )
}
