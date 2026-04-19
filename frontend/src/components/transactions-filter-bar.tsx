import { useMemo, useRef, useState } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { format, startOfMonth, startOfYear, subDays } from 'date-fns'
import {
  Calendar as CalendarIcon,
  Check,
  ChevronRight,
  ListFilter,
  Search,
  Store,
  Tag,
  Wallet,
  X,
} from 'lucide-react'
import { ptBR, enUS } from 'date-fns/locale'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuPortal,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from '@/components/ui/popover'
import { Calendar } from '@/components/ui/calendar'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { Account, Category, Payee } from '@/types'

interface TransactionsFilterBarProps {
  searchInput: string
  onSearchChange: (value: string) => void
  filterAccountIds: string[]
  onAccountIdsChange: (value: string[]) => void
  filterCategoryIds: string[]
  onCategoryIdsChange: (value: string[]) => void
  filterUncategorized: boolean
  onUncategorizedChange: (value: boolean) => void
  filterPayee: string
  onPayeeChange: (value: string) => void
  filterFrom: string
  filterTo: string
  onDateRangeChange: (from: string, to: string) => void
  onClearAll: () => void
  accounts: Account[]
  categories: Category[]
  payees: Payee[]
}

function toISODate(d: Date): string {
  return format(d, 'yyyy-MM-dd')
}

function toggleInArray(arr: string[], id: string): string[] {
  return arr.includes(id) ? arr.filter((x) => x !== id) : [...arr, id]
}

export function TransactionsFilterBar({
  searchInput,
  onSearchChange,
  filterAccountIds,
  onAccountIdsChange,
  filterCategoryIds,
  onCategoryIdsChange,
  filterUncategorized,
  onUncategorizedChange,
  filterPayee,
  onPayeeChange,
  filterFrom,
  filterTo,
  onDateRangeChange,
  onClearAll,
  accounts,
  categories,
  payees,
}: TransactionsFilterBarProps) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const dateFnsLocale = i18n.language === 'pt-BR' ? ptBR : enUS
  const [menuOpen, setMenuOpen] = useState(false)
  const [accountSubOpen, setAccountSubOpen] = useState(false)
  const [categorySubOpen, setCategorySubOpen] = useState(false)
  const keepAccountSubOpenRef = useRef(false)
  const keepCategorySubOpenRef = useRef(false)
  const [dateCustomOpen, setDateCustomOpen] = useState(false)
  const [draftFrom, setDraftFrom] = useState<string>(filterFrom)
  const [draftTo, setDraftTo] = useState<string>(filterTo)
  const searchRef = useRef<HTMLInputElement>(null)

  // When a CheckRow is clicked inside a submenu, Radix tries to close the submenu
  // even if we preventDefault in onSelect. We intercept the close request so the
  // submenu stays open and users can toggle several rows in a row.
  const handleAccountSubOpenChange = (open: boolean) => {
    if (!open && keepAccountSubOpenRef.current) {
      keepAccountSubOpenRef.current = false
      return
    }
    setAccountSubOpen(open)
  }
  const handleCategorySubOpenChange = (open: boolean) => {
    if (!open && keepCategorySubOpenRef.current) {
      keepCategorySubOpenRef.current = false
      return
    }
    setCategorySubOpen(open)
  }
  // When the root menu closes, make sure submenus close too so a fresh open starts clean.
  const handleMenuOpenChange = (open: boolean) => {
    setMenuOpen(open)
    if (!open) {
      setAccountSubOpen(false)
      setCategorySubOpen(false)
      keepAccountSubOpenRef.current = false
      keepCategorySubOpenRef.current = false
    }
  }

  const accountById = useMemo(() => {
    const map = new Map<string, Account>()
    accounts.forEach((a) => map.set(a.id, a))
    return map
  }, [accounts])

  const categoryById = useMemo(() => {
    const map = new Map<string, Category>()
    categories.forEach((c) => map.set(c.id, c))
    return map
  }, [categories])

  const selectedPayee = useMemo(
    () => payees.find((p) => p.id === filterPayee),
    [payees, filterPayee],
  )

  const hasAnyFilter =
    filterAccountIds.length > 0 ||
    filterCategoryIds.length > 0 ||
    filterUncategorized ||
    !!filterPayee ||
    !!filterFrom ||
    !!filterTo ||
    searchInput.trim().length > 0

  const dateLabel = useMemo(() => {
    if (!filterFrom && !filterTo) return null
    const fmt = (iso: string) =>
      new Date(iso + 'T00:00:00').toLocaleDateString(locale, {
        day: '2-digit',
        month: 'short',
      })
    if (filterFrom && filterTo) return `${fmt(filterFrom)} — ${fmt(filterTo)}`
    if (filterFrom) return `≥ ${fmt(filterFrom)}`
    return `≤ ${fmt(filterTo)}`
  }, [filterFrom, filterTo, locale])

  const datePresets = useMemo(() => {
    const today = new Date()
    return [
      {
        key: 'today',
        label: t('transactions.filtersBar.datePresets.today'),
        from: toISODate(today),
        to: toISODate(today),
      },
      {
        key: 'last7',
        label: t('transactions.filtersBar.datePresets.last7'),
        from: toISODate(subDays(today, 6)),
        to: toISODate(today),
      },
      {
        key: 'last30',
        label: t('transactions.filtersBar.datePresets.last30'),
        from: toISODate(subDays(today, 29)),
        to: toISODate(today),
      },
      {
        key: 'thisMonth',
        label: t('transactions.filtersBar.datePresets.thisMonth'),
        from: toISODate(startOfMonth(today)),
        to: toISODate(today),
      },
      {
        key: 'last90',
        label: t('transactions.filtersBar.datePresets.last90'),
        from: toISODate(subDays(today, 89)),
        to: toISODate(today),
      },
      {
        key: 'thisYear',
        label: t('transactions.filtersBar.datePresets.thisYear'),
        from: toISODate(startOfYear(today)),
        to: toISODate(today),
      },
    ]
  }, [t])

  const openCustomRange = () => {
    setDraftFrom(filterFrom)
    setDraftTo(filterTo)
    setMenuOpen(false)
    // Wait for the dropdown to finish closing before showing the popover
    // so focus and portal state settle correctly.
    setTimeout(() => setDateCustomOpen(true), 80)
  }

  const accountSummary =
    filterAccountIds.length > 1
      ? t('transactions.filtersBar.nSelected', { count: filterAccountIds.length })
      : filterAccountIds.length === 1
        ? (accountById.get(filterAccountIds[0])?.name ?? '')
        : ''

  const categorySummary = (() => {
    const total = filterCategoryIds.length + (filterUncategorized ? 1 : 0)
    if (total > 1)
      return t('transactions.filtersBar.nSelected', { count: total })
    if (filterUncategorized) return t('transactions.uncategorized')
    if (filterCategoryIds.length === 1)
      return categoryById.get(filterCategoryIds[0])?.name ?? ''
    return ''
  })()

  return (
    <div className="mb-4">
      <Popover open={dateCustomOpen} onOpenChange={setDateCustomOpen} modal={true}>
      <PopoverAnchor asChild>
      <div
        className={cn(
          'group/filterbar rounded-xl border border-border bg-card shadow-sm transition-colors',
          'focus-within:border-primary/40 focus-within:ring-[3px] focus-within:ring-primary/10',
        )}
      >
        {/* Top row: search input + controls */}
        <div className="flex items-center gap-1.5 px-2 py-1.5">
        {/* Search input */}
        <div className="relative flex min-w-0 flex-1 items-center">
          <Search
            size={15}
            className="pointer-events-none absolute left-2.5 text-muted-foreground/70"
          />
          <Input
            ref={searchRef}
            type="text"
            placeholder={t('transactions.searchPlaceholder')}
            value={searchInput}
            onChange={(e) => onSearchChange(e.target.value)}
            className="h-9 w-full border-0 bg-transparent pl-8 pr-2 text-[13.5px] shadow-none focus-visible:ring-0 focus-visible:border-transparent"
          />
        </div>

        {/* Right-side controls */}
        <div className="ml-auto flex shrink-0 items-center gap-1 pl-1">
          {hasAnyFilter && (
            <button
              type="button"
              onClick={onClearAll}
              className="hidden h-7 items-center rounded-md px-2 text-[11.5px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:inline-flex"
            >
              {t('transactions.clearFilters')}
            </button>
          )}

          <DropdownMenu open={menuOpen} onOpenChange={handleMenuOpenChange}>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={t('transactions.filtersBar.filters')}
                className={cn(
                  'inline-flex h-8 items-center gap-1.5 rounded-md border border-border/80 bg-background px-2.5 text-[12px] font-medium text-muted-foreground transition-colors',
                  'hover:bg-muted hover:text-foreground',
                  menuOpen && 'bg-muted text-foreground',
                  hasAnyFilter && 'border-primary/30 text-primary hover:text-primary',
                )}
              >
                <ListFilter size={13} />
                <span className="hidden sm:inline">
                  {t('transactions.filtersBar.filters')}
                </span>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              sideOffset={6}
              className="w-[240px] p-1"
            >
              <DropdownMenuLabel className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
                {t('transactions.filtersBar.filterBy')}
              </DropdownMenuLabel>
              <DropdownMenuGroup>
                {/* Account submenu (multi) */}
                <DropdownMenuSub
                  open={accountSubOpen}
                  onOpenChange={handleAccountSubOpenChange}
                >
                  <DropdownMenuSubTrigger className="gap-2 text-[13px]">
                    <Wallet size={14} className="text-muted-foreground" />
                    <span className="flex-1">{t('transactions.account')}</span>
                    {accountSummary && (
                      <span className="max-w-[90px] truncate text-[11px] text-muted-foreground">
                        {accountSummary}
                      </span>
                    )}
                  </DropdownMenuSubTrigger>
                  <DropdownMenuPortal>
                    <DropdownMenuSubContent
                      sideOffset={8}
                      className="max-h-[320px] w-[240px] overflow-y-auto p-1"
                    >
                      {accounts.length === 0 ? (
                        <div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
                          {t('transactions.filtersBar.noOptions')}
                        </div>
                      ) : (
                        accounts.map((a) => (
                          <CheckRow
                            key={a.id}
                            checked={filterAccountIds.includes(a.id)}
                            onToggle={() => {
                              keepAccountSubOpenRef.current = true
                              onAccountIdsChange(
                                toggleInArray(filterAccountIds, a.id),
                              )
                            }}
                            label={a.name}
                            sublabel={a.currency}
                          />
                        ))
                      )}
                      {filterAccountIds.length > 0 && (
                        <>
                          <div className="my-1 h-px bg-border/60" />
                          <DropdownMenuItem
                            onSelect={(e) => {
                              e.preventDefault()
                              keepAccountSubOpenRef.current = true
                              onAccountIdsChange([])
                            }}
                            className="gap-2 rounded-sm px-2 py-1.5 text-[12px] text-muted-foreground"
                          >
                            <X size={12} />
                            {t('transactions.filtersBar.clearSelection')}
                          </DropdownMenuItem>
                        </>
                      )}
                    </DropdownMenuSubContent>
                  </DropdownMenuPortal>
                </DropdownMenuSub>

                {/* Category submenu (multi) */}
                <DropdownMenuSub
                  open={categorySubOpen}
                  onOpenChange={handleCategorySubOpenChange}
                >
                  <DropdownMenuSubTrigger className="gap-2 text-[13px]">
                    <Tag size={14} className="text-muted-foreground" />
                    <span className="flex-1">{t('transactions.category')}</span>
                    {categorySummary && (
                      <span className="max-w-[90px] truncate text-[11px] text-muted-foreground">
                        {categorySummary}
                      </span>
                    )}
                  </DropdownMenuSubTrigger>
                  <DropdownMenuPortal>
                    <DropdownMenuSubContent
                      sideOffset={8}
                      className="max-h-[320px] w-[240px] overflow-y-auto p-1"
                    >
                      <CheckRow
                        checked={filterUncategorized}
                        onToggle={() => {
                          keepCategorySubOpenRef.current = true
                          onUncategorizedChange(!filterUncategorized)
                        }}
                        label={t('transactions.uncategorized')}
                        italic
                      />
                      <div className="my-1 h-px bg-border/60" />
                      {categories.length === 0 ? (
                        <div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
                          {t('transactions.filtersBar.noOptions')}
                        </div>
                      ) : (
                        categories.map((c) => (
                          <CheckRow
                            key={c.id}
                            checked={filterCategoryIds.includes(c.id)}
                            onToggle={() => {
                              keepCategorySubOpenRef.current = true
                              onCategoryIdsChange(
                                toggleInArray(filterCategoryIds, c.id),
                              )
                            }}
                            label={c.name}
                            swatchColor={c.color ?? undefined}
                          />
                        ))
                      )}
                      {(filterCategoryIds.length > 0 || filterUncategorized) && (
                        <>
                          <div className="my-1 h-px bg-border/60" />
                          <DropdownMenuItem
                            onSelect={(e) => {
                              e.preventDefault()
                              keepCategorySubOpenRef.current = true
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
                    </DropdownMenuSubContent>
                  </DropdownMenuPortal>
                </DropdownMenuSub>

                {/* Payee submenu (single) */}
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger className="gap-2 text-[13px]">
                    <Store size={14} className="text-muted-foreground" />
                    <span className="flex-1">{t('payees.payee')}</span>
                    {selectedPayee && (
                      <span className="max-w-[90px] truncate text-[11px] text-muted-foreground">
                        {selectedPayee.name}
                      </span>
                    )}
                  </DropdownMenuSubTrigger>
                  <DropdownMenuPortal>
                    <DropdownMenuSubContent
                      sideOffset={8}
                      className="max-h-[320px] w-[240px] overflow-y-auto p-1"
                    >
                      <DropdownMenuItem
                        onSelect={() => onPayeeChange('')}
                        className={cn(
                          'gap-2 rounded-sm px-2 py-1.5 text-[13px]',
                          !filterPayee && 'bg-primary/5',
                        )}
                      >
                        <span className="size-2.5 shrink-0" />
                        <span className="min-w-0 flex-1 truncate text-left">
                          {t('transactions.all')}
                        </span>
                        {!filterPayee && <Check size={13} className="text-primary" />}
                      </DropdownMenuItem>
                      <div className="my-1 h-px bg-border/60" />
                      {payees.length === 0 ? (
                        <div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
                          {t('transactions.filtersBar.noOptions')}
                        </div>
                      ) : (
                        payees.map((p) => (
                          <DropdownMenuItem
                            key={p.id}
                            onSelect={() => onPayeeChange(p.id)}
                            className={cn(
                              'gap-2 rounded-sm px-2 py-1.5 text-[13px]',
                              filterPayee === p.id && 'bg-primary/5',
                            )}
                          >
                            <span className="size-2.5 shrink-0" />
                            <span className="min-w-0 flex-1 truncate text-left">
                              {p.name}
                            </span>
                            {filterPayee === p.id && (
                              <Check size={13} className="text-primary" />
                            )}
                          </DropdownMenuItem>
                        ))
                      )}
                    </DropdownMenuSubContent>
                  </DropdownMenuPortal>
                </DropdownMenuSub>

                {/* Date range submenu with presets */}
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger className="gap-2 text-[13px]">
                    <CalendarIcon size={14} className="text-muted-foreground" />
                    <span className="flex-1">
                      {t('transactions.filtersBar.date')}
                    </span>
                    {dateLabel && (
                      <span className="max-w-[90px] truncate text-[11px] text-muted-foreground">
                        {dateLabel}
                      </span>
                    )}
                  </DropdownMenuSubTrigger>
                  <DropdownMenuPortal>
                    <DropdownMenuSubContent
                      sideOffset={8}
                      className="w-[220px] p-1"
                    >
                      <DropdownMenuItem
                        onSelect={() => onDateRangeChange('', '')}
                        className={cn(
                          'gap-2 rounded-sm px-2 py-1.5 text-[13px]',
                          !filterFrom && !filterTo && 'bg-primary/5',
                        )}
                      >
                        <span className="size-2.5 shrink-0" />
                        <span className="min-w-0 flex-1 truncate text-left">
                          {t('transactions.all')}
                        </span>
                        {!filterFrom && !filterTo && (
                          <Check size={13} className="text-primary" />
                        )}
                      </DropdownMenuItem>
                      <div className="my-1 h-px bg-border/60" />
                      {datePresets.map((preset) => {
                        const active =
                          filterFrom === preset.from && filterTo === preset.to
                        return (
                          <DropdownMenuItem
                            key={preset.key}
                            onSelect={() =>
                              onDateRangeChange(preset.from, preset.to)
                            }
                            className={cn(
                              'gap-2 rounded-sm px-2 py-1.5 text-[13px]',
                              active && 'bg-primary/5',
                            )}
                          >
                            <span className="size-2.5 shrink-0" />
                            <span className="min-w-0 flex-1 truncate text-left">
                              {preset.label}
                            </span>
                            {active && <Check size={13} className="text-primary" />}
                          </DropdownMenuItem>
                        )
                      })}
                      <div className="my-1 h-px bg-border/60" />
                      <DropdownMenuItem
                        onSelect={openCustomRange}
                        className="justify-between rounded-sm px-2 py-1.5 text-[13px]"
                      >
                        <span>{t('transactions.filtersBar.customRange')}</span>
                        <ChevronRight
                          size={13}
                          className="text-muted-foreground/60"
                        />
                      </DropdownMenuItem>
                    </DropdownMenuSubContent>
                  </DropdownMenuPortal>
                </DropdownMenuSub>
              </DropdownMenuGroup>

              {hasAnyFilter && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onSelect={() => {
                      onClearAll()
                      setMenuOpen(false)
                    }}
                    className="gap-2 rounded-sm px-2 py-1.5 text-[12.5px] text-muted-foreground"
                  >
                    <X size={13} />
                    {t('transactions.clearFilters')}
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        </div>

        {/* Bottom row: active filter chips (only when any are set) */}
        {(filterAccountIds.length > 0 ||
          filterCategoryIds.length > 0 ||
          filterUncategorized ||
          !!selectedPayee ||
          !!dateLabel) && (
          <div className="flex flex-wrap items-center gap-1 border-t border-border/60 px-2 py-1.5">
            {filterAccountIds.map((id) => {
              const account = accountById.get(id)
              if (!account) return null
              return (
                <FilterChip
                  key={`acc-${id}`}
                  icon={<Wallet size={12} />}
                  label={t('transactions.account')}
                  value={getAccountName(account)}
                  onRemove={() =>
                    onAccountIdsChange(filterAccountIds.filter((x) => x !== id))
                  }
                />
              )
            })}
            {filterCategoryIds.map((id) => {
              const cat = categoryById.get(id)
              if (!cat) return null
              return (
                <FilterChip
                  key={`cat-${id}`}
                  icon={<Tag size={12} />}
                  label={t('transactions.category')}
                  value={cat.name}
                  tint={cat.color ?? undefined}
                  onRemove={() =>
                    onCategoryIdsChange(
                      filterCategoryIds.filter((x) => x !== id),
                    )
                  }
                />
              )
            })}
            {filterUncategorized && (
              <FilterChip
                icon={<Tag size={12} />}
                label={t('transactions.category')}
                value={t('transactions.uncategorized')}
                onRemove={() => onUncategorizedChange(false)}
              />
            )}
            {selectedPayee && (
              <FilterChip
                icon={<Store size={12} />}
                label={t('payees.payee')}
                value={selectedPayee.name}
                onRemove={() => onPayeeChange('')}
              />
            )}
            {dateLabel && (
              <FilterChip
                icon={<CalendarIcon size={12} />}
                label={t('transactions.filtersBar.date')}
                value={dateLabel}
                onRemove={() => onDateRangeChange('', '')}
              />
            )}
          </div>
        )}
      </div>

      </PopoverAnchor>
        {/* Custom range popover — anchored to the filter bar above */}
        <PopoverContent
          align="end"
          sideOffset={8}
          className="w-auto p-0"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <div className="border-b border-border/70 px-4 py-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
              {t('transactions.filtersBar.customRange')}
            </p>
            <p className="mt-0.5 text-[11px] text-muted-foreground/70">
              {draftFrom || draftTo
                ? formatRange(draftFrom, draftTo, locale)
                : t('transactions.filtersBar.pickRange')}
            </p>
          </div>
          <div className="flex flex-col gap-4 p-3 sm:flex-row sm:gap-0">
            <div className="sm:border-r sm:border-border/60 sm:pr-2">
              <p className="px-2 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">
                {t('transactions.filtersBar.fromLabel')}
              </p>
              <Calendar
                selected={draftFrom ? new Date(draftFrom + 'T00:00:00') : undefined}
                defaultMonth={
                  draftFrom ? new Date(draftFrom + 'T00:00:00') : new Date()
                }
                locale={dateFnsLocale}
                onSelect={(d) => setDraftFrom(d ? toISODate(d) : '')}
              />
            </div>
            <div className="sm:pl-2">
              <p className="px-2 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">
                {t('transactions.filtersBar.toLabel')}
              </p>
              <Calendar
                selected={draftTo ? new Date(draftTo + 'T00:00:00') : undefined}
                defaultMonth={
                  draftTo
                    ? new Date(draftTo + 'T00:00:00')
                    : draftFrom
                      ? new Date(draftFrom + 'T00:00:00')
                      : new Date()
                }
                locale={dateFnsLocale}
                onSelect={(d) => setDraftTo(d ? toISODate(d) : '')}
              />
            </div>
          </div>
          <div className="flex items-center justify-between gap-2 border-t border-border/70 px-3 py-2">
            <button
              type="button"
              onClick={() => {
                setDraftFrom('')
                setDraftTo('')
              }}
              className="text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              {t('transactions.filtersBar.reset')}
            </button>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setDateCustomOpen(false)}
              >
                {t('transactions.filtersBar.cancel')}
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={!draftFrom && !draftTo}
                onClick={() => {
                  // Normalize: if user only picked one of the two, mirror it.
                  const from = draftFrom || draftTo
                  const to = draftTo || draftFrom
                  if (from && to && from > to) {
                    onDateRangeChange(to, from)
                  } else {
                    onDateRangeChange(from, to)
                  }
                  setDateCustomOpen(false)
                }}
              >
                {t('transactions.filtersBar.apply')}
              </Button>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}

function formatRange(from: string, to: string, locale: string): string {
  const fmt = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString(locale, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    })
  if (from && to) return `${fmt(from)} — ${fmt(to)}`
  if (from) return `≥ ${fmt(from)}`
  return `≤ ${fmt(to)}`
}

interface FilterChipProps {
  icon: React.ReactNode
  label: string
  value: string
  tint?: string
  onRemove: () => void
}

function FilterChip({ icon, label, value, tint, onRemove }: FilterChipProps) {
  return (
    <button
      type="button"
      onClick={onRemove}
      className="group inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border border-border/80 bg-muted/50 pl-2 pr-1.5 text-[11.5px] text-foreground transition-colors hover:border-destructive/40 hover:bg-destructive/5"
      style={tint ? { borderColor: `${tint}55`, backgroundColor: `${tint}12` } : undefined}
    >
      <span
        className="flex items-center text-muted-foreground group-hover:text-destructive"
        style={tint ? { color: tint } : undefined}
      >
        {icon}
      </span>
      <span className="text-muted-foreground">{label}:</span>
      <span className="max-w-[140px] truncate font-medium text-foreground">
        {value}
      </span>
      <span className="ml-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full text-muted-foreground/70 group-hover:text-destructive">
        <X size={11} />
      </span>
    </button>
  )
}

interface CheckRowProps {
  checked: boolean
  onToggle: () => void
  label: string
  sublabel?: string
  swatchColor?: string
  italic?: boolean
}

function CheckRow({
  checked,
  onToggle,
  label,
  sublabel,
  swatchColor,
  italic,
}: CheckRowProps) {
  return (
    <DropdownMenuItem
      onSelect={(e) => {
        // Keep the menu open so users can select multiple options.
        e.preventDefault()
        onToggle()
      }}
      className={cn(
        'gap-2 rounded-sm px-2 py-1.5 text-[13px]',
        checked && 'bg-primary/5 data-[highlighted]:bg-primary/10',
      )}
    >
      <span
        className={cn(
          'flex size-[14px] shrink-0 items-center justify-center rounded-[4px] border transition-colors',
          checked
            ? 'border-primary bg-primary text-primary-foreground'
            : 'border-border/80 bg-background',
        )}
      >
        {checked && <Check size={10} strokeWidth={3} />}
      </span>
      {swatchColor ? (
        <span
          className="size-2.5 shrink-0 rounded-full border border-black/5"
          style={{ backgroundColor: swatchColor }}
        />
      ) : null}
      <span
        className={cn(
          'min-w-0 flex-1 truncate text-left',
          italic && 'italic text-muted-foreground',
        )}
      >
        {label}
      </span>
      {sublabel && (
        <span className="text-[10.5px] uppercase tracking-wide text-muted-foreground/70">
          {sublabel}
        </span>
      )}
    </DropdownMenuItem>
  )
}
