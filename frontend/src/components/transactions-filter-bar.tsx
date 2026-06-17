import { useMemo, useRef, useState } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { format, startOfMonth, startOfYear, subDays } from 'date-fns'
import {
  ArrowUpDown,
  Calendar as CalendarIcon,
  Check,
  ChevronRight,
  Coins,
  ListFilter,
  Search,
  Store,
  Tag,
  Users,
  Wallet,
  X,
} from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
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
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { CategoryFilterContent } from '@/components/category-filter-content'
import type { Account, Category, CategoryGroup, Group, Payee } from '@/types'

interface TransactionsFilterBarProps {
  searchInput: string
  onSearchChange: (value: string) => void
  onSearchSubmit?: (value: string) => void
  filterAccountIds: string[]
  onAccountIdsChange: (value: string[]) => void
  filterCategoryIds: string[]
  onCategoryIdsChange: (value: string[]) => void
  filterUncategorized: boolean
  onUncategorizedChange: (value: boolean) => void
  filterPayee: string
  onPayeeChange: (value: string) => void
  filterGroupId: string
  onGroupIdChange: (value: string) => void
  filterType: string
  onTypeChange: (value: string) => void
  filterFrom: string
  filterTo: string
  onDateRangeChange: (from: string, to: string) => void
  filterMinAmount: string
  filterMaxAmount: string
  onAmountRangeChange: (min: string, max: string) => void
  onClearAll: () => void
  accounts: Account[]
  categories: Category[]
  categoryGroups: CategoryGroup[]
  payees: Payee[]
  groups: Group[]
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
  onSearchSubmit,
  filterAccountIds,
  onAccountIdsChange,
  filterCategoryIds,
  onCategoryIdsChange,
  filterUncategorized,
  onUncategorizedChange,
  filterPayee,
  onPayeeChange,
  filterGroupId,
  onGroupIdChange,
  filterType,
  onTypeChange,
  filterFrom,
  filterTo,
  onDateRangeChange,
  filterMinAmount,
  filterMaxAmount,
  onAmountRangeChange,
  onClearAll,
  accounts,
  categories,
  categoryGroups,
  payees,
  groups,
}: TransactionsFilterBarProps) {
  const { t, i18n } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const dateFnsLocale = resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language)
  const [menuOpen, setMenuOpen] = useState(false)
  const [accountSubOpen, setAccountSubOpen] = useState(false)
  const [categorySubOpen, setCategorySubOpen] = useState(false)
  const keepAccountSubOpenRef = useRef(false)
  const keepCategorySubOpenRef = useRef(false)
  const [dateCustomOpen, setDateCustomOpen] = useState(false)
  const [draftFrom, setDraftFrom] = useState<string>(filterFrom)
  const [draftTo, setDraftTo] = useState<string>(filterTo)
  const [amountSubOpen, setAmountSubOpen] = useState(false)
  const [draftMinAmount, setDraftMinAmount] = useState<string>(filterMinAmount)
  const [draftMaxAmount, setDraftMaxAmount] = useState<string>(filterMaxAmount)
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
      setAmountSubOpen(false)
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

  const selectedGroup = useMemo(
    () => groups.find((g) => g.id === filterGroupId),
    [groups, filterGroupId],
  )

  const hasAnyFilter =
    filterAccountIds.length > 0 ||
    filterCategoryIds.length > 0 ||
    filterUncategorized ||
    !!filterPayee ||
    !!filterGroupId ||
    !!filterType ||
    !!filterFrom ||
    !!filterTo ||
    !!filterMinAmount ||
    !!filterMaxAmount ||
    searchInput.trim().length > 0

  const typeLabel =
    filterType === 'credit'
      ? t('transactions.income')
      : filterType === 'debit'
        ? t('transactions.expense')
        : ''

  const dateLabel = useMemo(() => {
    if (!filterFrom && !filterTo) return null
    const fmt = (iso: string) =>
      new Date(iso + 'T00:00:00').toLocaleDateString(dateLocale, {
        day: '2-digit',
        month: 'short',
      })
    if (filterFrom && filterTo) return `${fmt(filterFrom)} — ${fmt(filterTo)}`
    if (filterFrom) return `≥ ${fmt(filterFrom)}`
    return `≤ ${fmt(filterTo)}`
  }, [filterFrom, filterTo, dateLocale])

  const amountLabel = useMemo(() => {
    if (!filterMinAmount && !filterMaxAmount) return null
    const fmt = (raw: string) => {
      const n = Number(raw)
      if (!Number.isFinite(n)) return raw
      return n.toLocaleString(locale, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    }
    if (filterMinAmount && filterMaxAmount) {
      if (filterMinAmount === filterMaxAmount) return `= ${fmt(filterMinAmount)}`
      return `${fmt(filterMinAmount)} — ${fmt(filterMaxAmount)}`
    }
    if (filterMinAmount) return `≥ ${fmt(filterMinAmount)}`
    return `≤ ${fmt(filterMaxAmount)}`
  }, [filterMinAmount, filterMaxAmount, locale])

  const applyAmountRange = () => {
    const normalize = (raw: string) => raw.trim().replace(',', '.')
    const min = normalize(draftMinAmount)
    const max = normalize(draftMaxAmount)
    const minOk = min === '' || (Number.isFinite(Number(min)) && Number(min) >= 0)
    const maxOk = max === '' || (Number.isFinite(Number(max)) && Number(max) >= 0)
    if (!minOk || !maxOk) return
    // Swap if user inverted the range so the filter still makes sense.
    if (min && max && Number(min) > Number(max)) {
      onAmountRangeChange(max, min)
    } else {
      onAmountRangeChange(min, max)
    }
    setAmountSubOpen(false)
    setMenuOpen(false)
  }

  const handleAmountSubOpenChange = (open: boolean) => {
    if (open) {
      setDraftMinAmount(filterMinAmount)
      setDraftMaxAmount(filterMaxAmount)
    }
    setAmountSubOpen(open)
  }

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
        ? (getAccountName(accountById.get(filterAccountIds[0]) ?? { name: '', display_name: null }))
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
        {/* Search input — splits committed `#tag` tokens into inline chips,
            keeping free text as plain typing. Comma or space commits a token. */}
        <SearchWithTagChips
          inputRef={searchRef}
          value={searchInput}
          placeholder={t('transactions.searchPlaceholder')}
          onChange={onSearchChange}
          onSubmit={onSearchSubmit}
        />

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
                          <DropdownMenuCheckboxItem
                            key={a.id}
                            checked={filterAccountIds.includes(a.id)}
                            onSelect={(e) => {
                              e.preventDefault()
                              keepAccountSubOpenRef.current = true
                              onAccountIdsChange(
                                toggleInArray(filterAccountIds, a.id),
                              )
                            }}
                            className="gap-2 rounded-sm py-1.5 text-[13px]"
                          >
                            <span className="min-w-0 flex-1 truncate text-left">
                              {getAccountName(a)}
                            </span>
                            {a.currency && (
                              <span className="text-[10.5px] uppercase tracking-wide text-muted-foreground/70">
                                {a.currency}
                              </span>
                            )}
                          </DropdownMenuCheckboxItem>
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
                      <CategoryFilterContent
                        categoryIds={filterCategoryIds}
                        onCategoryIdsChange={onCategoryIdsChange}
                        filterUncategorized={filterUncategorized}
                        onUncategorizedChange={onUncategorizedChange}
                        categories={categories}
                        groups={categoryGroups}
                        onKeepOpen={() => { keepCategorySubOpenRef.current = true }}
                      />
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

                {/* Group submenu (single) */}
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger className="gap-2 text-[13px]">
                    <Users size={14} className="text-muted-foreground" />
                    <span className="flex-1">{t('splitGroups.group')}</span>
                    {selectedGroup && (
                      <span className="max-w-[90px] truncate text-[11px] text-muted-foreground">
                        {selectedGroup.name}
                      </span>
                    )}
                  </DropdownMenuSubTrigger>
                  <DropdownMenuPortal>
                    <DropdownMenuSubContent
                      sideOffset={8}
                      className="max-h-[320px] w-[240px] overflow-y-auto p-1"
                    >
                      <DropdownMenuItem
                        onSelect={() => onGroupIdChange('')}
                        className={cn(
                          'gap-2 rounded-sm px-2 py-1.5 text-[13px]',
                          !filterGroupId && 'bg-primary/5',
                        )}
                      >
                        <span className="size-2.5 shrink-0" />
                        <span className="min-w-0 flex-1 truncate text-left">
                          {t('transactions.all')}
                        </span>
                        {!filterGroupId && <Check size={13} className="text-primary" />}
                      </DropdownMenuItem>
                      <div className="my-1 h-px bg-border/60" />
                      {groups.length === 0 ? (
                        <div className="px-2 py-3 text-center text-[12px] text-muted-foreground">
                          {t('transactions.filtersBar.noOptions')}
                        </div>
                      ) : (
                        groups.map((g) => (
                          <DropdownMenuItem
                            key={g.id}
                            onSelect={() => onGroupIdChange(g.id)}
                            className={cn(
                              'gap-2 rounded-sm px-2 py-1.5 text-[13px]',
                              filterGroupId === g.id && 'bg-primary/5',
                            )}
                          >
                            <span className="size-2.5 shrink-0" />
                            <span className="min-w-0 flex-1 truncate text-left">
                              {g.name}
                            </span>
                            {filterGroupId === g.id && (
                              <Check size={13} className="text-primary" />
                            )}
                          </DropdownMenuItem>
                        ))
                      )}
                    </DropdownMenuSubContent>
                  </DropdownMenuPortal>
                </DropdownMenuSub>

                {/* Type submenu (single — income vs expense) */}
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger className="gap-2 text-[13px]">
                    <ArrowUpDown size={14} className="text-muted-foreground" />
                    <span className="flex-1">{t('transactions.type')}</span>
                    {typeLabel && (
                      <span className="max-w-[90px] truncate text-[11px] text-muted-foreground">
                        {typeLabel}
                      </span>
                    )}
                  </DropdownMenuSubTrigger>
                  <DropdownMenuPortal>
                    <DropdownMenuSubContent
                      sideOffset={8}
                      className="w-[200px] p-1"
                    >
                      {[
                        { value: '', label: t('transactions.all') },
                        { value: 'credit', label: t('transactions.income') },
                        { value: 'debit', label: t('transactions.expense') },
                      ].map((opt) => (
                        <DropdownMenuItem
                          key={opt.value || 'all'}
                          onSelect={() => onTypeChange(opt.value)}
                          className={cn(
                            'gap-2 rounded-sm px-2 py-1.5 text-[13px]',
                            filterType === opt.value && 'bg-primary/5',
                          )}
                        >
                          <span className="size-2.5 shrink-0" />
                          <span className="min-w-0 flex-1 truncate text-left">
                            {opt.label}
                          </span>
                          {filterType === opt.value && (
                            <Check size={13} className="text-primary" />
                          )}
                        </DropdownMenuItem>
                      ))}
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

                {/* Amount range submenu — exact match by setting min=max */}
                <DropdownMenuSub
                  open={amountSubOpen}
                  onOpenChange={handleAmountSubOpenChange}
                >
                  <DropdownMenuSubTrigger className="gap-2 text-[13px]">
                    <Coins size={14} className="text-muted-foreground" />
                    <span className="flex-1">
                      {t('transactions.filtersBar.amount')}
                    </span>
                    {amountLabel && (
                      <span className="max-w-[90px] truncate text-[11px] text-muted-foreground">
                        {amountLabel}
                      </span>
                    )}
                  </DropdownMenuSubTrigger>
                  <DropdownMenuPortal>
                    <DropdownMenuSubContent
                      sideOffset={8}
                      className="w-[260px] p-2"
                    >
                      <div className="flex items-center gap-2">
                        <div className="flex-1">
                          <label className="block px-1 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">
                            {t('transactions.filtersBar.amountMinLabel')}
                          </label>
                          <input
                            type="number"
                            inputMode="decimal"
                            min={0}
                            step="0.01"
                            placeholder="0.00"
                            value={draftMinAmount}
                            onChange={(e) => setDraftMinAmount(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                e.preventDefault()
                                applyAmountRange()
                              }
                            }}
                            className="h-8 w-full rounded-md border border-border bg-background px-2 text-[13px] outline-none focus:border-primary/60 focus:ring-[2px] focus:ring-primary/15"
                          />
                        </div>
                        <div className="flex-1">
                          <label className="block px-1 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">
                            {t('transactions.filtersBar.amountMaxLabel')}
                          </label>
                          <input
                            type="number"
                            inputMode="decimal"
                            min={0}
                            step="0.01"
                            placeholder="0.00"
                            value={draftMaxAmount}
                            onChange={(e) => setDraftMaxAmount(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                e.preventDefault()
                                applyAmountRange()
                              }
                            }}
                            className="h-8 w-full rounded-md border border-border bg-background px-2 text-[13px] outline-none focus:border-primary/60 focus:ring-[2px] focus:ring-primary/15"
                          />
                        </div>
                      </div>
                      <p className="mt-2 text-[10.5px] leading-snug text-muted-foreground/80">
                        {t('transactions.filtersBar.amountHint')}
                      </p>
                      <div className="mt-2 flex items-center justify-between gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            setDraftMinAmount('')
                            setDraftMaxAmount('')
                            onAmountRangeChange('', '')
                            setAmountSubOpen(false)
                            setMenuOpen(false)
                          }}
                          className="text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground"
                        >
                          {t('transactions.filtersBar.reset')}
                        </button>
                        <Button
                          type="button"
                          size="sm"
                          disabled={!draftMinAmount && !draftMaxAmount}
                          onClick={applyAmountRange}
                        >
                          {t('transactions.filtersBar.apply')}
                        </Button>
                      </div>
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
          !!typeLabel ||
          !!dateLabel ||
          !!amountLabel) && (
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
            {typeLabel && (
              <FilterChip
                icon={<ArrowUpDown size={12} />}
                label={t('transactions.type')}
                value={typeLabel}
                onRemove={() => onTypeChange('')}
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
            {amountLabel && (
              <FilterChip
                icon={<Coins size={12} />}
                label={t('transactions.filtersBar.amount')}
                value={amountLabel}
                onRemove={() => onAmountRangeChange('', '')}
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
                ? formatRange(draftFrom, draftTo, dateLocale)
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

// Search input with inline `#tag` chips. Free text is a normal input;
// `#`-prefixed words become purple chips when committed via comma, space
// (when token starts with `#`), or Enter, and immediately apply as filters.
function SearchWithTagChips({
  inputRef,
  value,
  placeholder,
  onChange,
  onSubmit,
}: {
  inputRef: React.RefObject<HTMLInputElement | null>
  value: string
  placeholder?: string
  onChange: (value: string) => void
  onSubmit?: (value: string) => void
}) {
  // Split into leading `#tag` chips (terminated by whitespace) + free text.
  // We only treat a `#tag` as a chip when it has a trailing whitespace —
  // otherwise the user is still typing it.
  const [chips, freeText] = (() => {
    const parts: string[] = []
    let rest = value
    while (true) {
      const m = rest.match(/^(#\S+)\s+/)
      if (!m) break
      parts.push(m[1])
      rest = rest.slice(m[0].length)
    }
    return [parts, rest] as const
  })()

  const rebuild = (nextChips: string[], nextFreeText: string): string => {
    const head = nextChips.length ? nextChips.join(' ') + ' ' : ''
    return head + nextFreeText
  }

  const removeChipAt = (index: number) => {
    const next = [...chips]
    next.splice(index, 1)
    onChange(rebuild(next, freeText))
    inputRef.current?.focus()
  }

  const commitTrailingTagInFreeText = (text: string): { newChips: string[]; rest: string } | null => {
    // Match a `#tag` that ends the string (just typed before comma/space/Enter).
    const m = text.match(/^(.*?)(\s|^)(#\S+)$/)
    if (!m) return null
    const before = (m[1] + m[2]).trimEnd()
    const tag = m[3]
    const newChips = [...chips, tag]
    return { newChips, rest: before }
  }

  return (
    <div
      className="relative flex min-w-0 flex-1 flex-wrap items-center gap-1 px-2.5 py-1 min-h-9 cursor-text"
      onClick={() => inputRef.current?.focus()}
    >
      <Search size={15} className="pointer-events-none shrink-0 text-muted-foreground/70" />
      {chips.map((tag, i) => (
        <span
          key={`${tag}-${i}`}
          className="inline-flex items-center gap-1 rounded-full border border-primary/15 bg-primary/5 px-2 py-0.5 text-[11.5px] font-medium text-primary"
        >
          {tag}
          <button
            type="button"
            tabIndex={-1}
            onClick={(e) => {
              e.stopPropagation()
              removeChipAt(i)
            }}
            className="text-primary/60 hover:text-primary"
          >
            <X size={10} />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        type="text"
        placeholder={chips.length === 0 ? placeholder : undefined}
        value={freeText}
        onChange={(e) => {
          const next = e.target.value
          // Comma right after a `#tag` token commits it. Other commas stay
          // as literal characters in the free-text search.
          if (next.endsWith(',')) {
            const beforeComma = next.slice(0, -1)
            const result = commitTrailingTagInFreeText(beforeComma)
            if (result) {
              const submitValue = rebuild(result.newChips, result.rest)
              if (onSubmit) onSubmit(submitValue)
              else onChange(submitValue)
              return
            }
          }
          onChange(rebuild(chips, next))
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && onSubmit) {
            e.preventDefault()
            // Promote a still-being-typed `#tag` at the end to a chip too.
            const result = commitTrailingTagInFreeText(freeText)
            const submitValue = result
              ? rebuild(result.newChips, result.rest)
              : rebuild(chips, freeText)
            onSubmit(submitValue)
          } else if (e.key === 'Backspace' && freeText === '' && chips.length > 0) {
            e.preventDefault()
            removeChipAt(chips.length - 1)
          } else if (e.key === ' ') {
            // Space after a `#tag` commits it; space inside free text is
            // a normal whitespace.
            const result = commitTrailingTagInFreeText(freeText)
            if (result) {
              e.preventDefault()
              const submitValue = rebuild(result.newChips, result.rest)
              if (onSubmit) onSubmit(submitValue)
              else onChange(submitValue)
            }
          }
        }}
        className="min-w-[80px] flex-1 border-0 bg-transparent text-[13.5px] outline-none placeholder:text-muted-foreground"
      />
    </div>
  )
}
