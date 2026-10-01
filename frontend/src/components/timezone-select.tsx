import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckIcon, ChevronDownIcon } from 'lucide-react'

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'
import { timezoneCityLabel, timezoneOffsetLabel, timezoneRegion } from '@/lib/timezone-utils'
import { cn, normalizeText } from '@/lib/utils'

interface TimezoneSelectProps {
  value: string
  onChange: (value: string) => void
  /** IANA names, as the server lists them. */
  options: string[]
  /**
   * An extra first choice carrying the empty value, for "follow the default"
   * pickers. Its label is shown on the trigger when nothing is chosen.
   */
  emptyOption?: string
  id?: string
  disabled?: boolean
  className?: string
  'aria-describedby'?: string
}

/**
 * A searchable timezone picker, grouped by region the way the category
 * picker groups by category group. Typing "sao paulo", "America/Sao" or
 * "gmt-3" all find America/Sao_Paulo; the flat 600-row dropdown it replaces
 * asked the user to scroll.
 */
export function TimezoneSelect({
  value,
  onChange,
  options,
  emptyOption,
  id,
  disabled = false,
  className,
  'aria-describedby': describedBy,
}: TimezoneSelectProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  const groups = useMemo(() => {
    const byRegion = new Map<string, string[]>()
    for (const name of options) {
      const region = timezoneRegion(name)
      byRegion.set(region, [...(byRegion.get(region) ?? []), name])
    }
    // Regions in alphabetical order; the few names without a region (UTC,
    // GMT and the legacy aliases) close the list.
    return [...byRegion.entries()]
      .sort(([a], [b]) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)))
      .map(([region, names]) => ({ region, names: [...names].sort() }))
  }, [options])

  // Offsets are computed once per open, not once per keystroke.
  const offsets = useMemo(() => {
    if (!open) return new Map<string, string | null>()
    const now = new Date()
    return new Map(options.map((name) => [name, timezoneOffsetLabel(name, now)]))
  }, [open, options])

  function handleOpenChange(next: boolean) {
    setOpen(next)
    if (!next) setSearch('')
  }

  function choose(next: string) {
    onChange(next)
    handleOpenChange(false)
  }

  const selectedOffset = value ? timezoneOffsetLabel(value) : null

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          id={id}
          role="combobox"
          aria-expanded={open}
          aria-describedby={describedBy}
          disabled={disabled}
          className={cn(
            'flex w-full items-center justify-between gap-2 rounded-md border border-input bg-card px-3 py-2 text-sm text-left shadow-xs transition-[color,box-shadow] outline-hidden focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30 dark:hover:bg-input/50 h-9 cursor-pointer',
            className,
          )}
        >
          <span className="flex items-center gap-2 min-w-0 truncate">
            {value ? (
              <>
                <span className="truncate">{value}</span>
                {selectedOffset && (
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{selectedOffset}</span>
                )}
              </>
            ) : (
              <span className={cn('truncate', emptyOption ? 'italic text-muted-foreground' : 'text-muted-foreground')}>
                {emptyOption ?? t('common.selectTimezone')}
              </span>
            )}
          </span>
          <ChevronDownIcon className="size-4 shrink-0 opacity-50" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[var(--radix-popover-trigger-width)] min-w-72 p-0 overflow-hidden"
      >
        <Command
          filter={(itemValue, query) => (normalizeText(itemValue).includes(normalizeText(query)) ? 1 : 0)}
        >
          <CommandInput
            placeholder={t('common.searchTimezone')}
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>{t('common.noTimezoneFound')}</CommandEmpty>
            {emptyOption && (
              <CommandGroup>
                <CommandItem
                  value={`default ${emptyOption}`}
                  onSelect={() => choose('')}
                  className="italic text-muted-foreground cursor-pointer"
                >
                  <span className="flex-1 truncate">{emptyOption}</span>
                  {value === '' && <CheckIcon className="size-4 shrink-0" />}
                </CommandItem>
              </CommandGroup>
            )}
            {groups.map(({ region, names }) => (
              <CommandGroup key={region || 'other'}>
                {region && (
                  <div className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
                    {region}
                  </div>
                )}
                {names.map((name) => {
                  const offset = offsets.get(name)
                  return (
                    <CommandItem
                      key={name}
                      value={`${name} ${timezoneCityLabel(name)} ${offset ?? ''}`}
                      onSelect={() => choose(name)}
                      className="cursor-pointer"
                    >
                      <span className="flex items-baseline gap-2 min-w-0 truncate flex-1">
                        <span className="truncate">{timezoneCityLabel(name)}</span>
                        <span className="truncate text-xs text-muted-foreground">{name}</span>
                      </span>
                      {offset && (
                        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{offset}</span>
                      )}
                      {value === name && <CheckIcon className="size-4 shrink-0" />}
                    </CommandItem>
                  )
                })}
              </CommandGroup>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
