import type { ComponentType, Dispatch, SetStateAction } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowUpDown,
  Calendar as CalendarIcon,
  Check,
  ChevronLeft,
  ChevronRight,
  Coins,
  EyeClosed,
  ListChecks,
  Store,
  Tag,
  Users,
  Wallet,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { CategoryFilterContent } from '@/components/category-filter-content'
import {
  DropdownMenuCheckboxItem,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'
import { getAccountName } from '@/lib/account-utils'
import { cn } from '@/lib/utils'
import type { Account, Category, CategoryGroup, Group, Payee } from '@/types'

export type MobileFilterView =
  | 'root'
  | 'account'
  | 'category'
  | 'payee'
  | 'group'
  | 'type'
  | 'status'
  | 'ignored'
  | 'date'
  | 'amount'

export interface MobileDatePreset {
  key: string
  label: string
  from: string
  to: string
}

interface MobileTransactionsFilterMenuProps {
  view: MobileFilterView
  setView: Dispatch<SetStateAction<MobileFilterView>>
  setMenuOpen: (open: boolean) => void
  accounts: Account[]
  categories: Category[]
  categoryGroups: CategoryGroup[]
  payees: Payee[]
  groups: Group[]
  accountIds: string[]
  categoryIds: string[]
  uncategorized: boolean
  payeeId: string
  groupId: string
  type: string
  status: string
  hideIgnored: boolean
  from: string
  to: string
  minAmount: string
  maxAmount: string
  appliedMinAmount: string
  appliedMaxAmount: string
  setMinAmount: (value: string) => void
  setMaxAmount: (value: string) => void
  summaries: Record<Exclude<MobileFilterView, 'root'>, string | null | undefined>
  datePresets: MobileDatePreset[]
  hasAnyFilter: boolean
  onAccountIdsChange: (value: string[]) => void
  onCategoryIdsChange: (value: string[]) => void
  onUncategorizedChange: (value: boolean) => void
  onPayeeChange: (value: string) => void
  onGroupIdChange: (value: string) => void
  onTypeChange: (value: string) => void
  onStatusChange: (value: string) => void
  onHideIgnoredChange: (value: boolean) => void
  onDateRangeChange: (from: string, to: string) => void
  onAmountRangeChange: (min: string, max: string) => void
  onApplyAmountRange: () => void
  onOpenCustomRange: () => void
  onClearAll: () => void
}

interface RootOption {
  view: Exclude<MobileFilterView, 'root'>
  icon: ComponentType<{ size?: number; className?: string }>
  label: string
  summary?: string | null
}

interface SelectionOption {
  value: string
  label: string
}

function toggleSelection(selectedIds: string[], id: string): string[] {
  return selectedIds.includes(id)
    ? selectedIds.filter((selectedId) => selectedId !== id)
    : [...selectedIds, id]
}

function MobileRootOption({
  option,
  onSelect,
}: {
  option: RootOption
  onSelect: (view: RootOption['view']) => void
}) {
  const Icon = option.icon
  return (
    <DropdownMenuItem
      onSelect={(event) => {
        event.preventDefault()
        onSelect(option.view)
      }}
      className="gap-2 py-2 text-[13px]"
    >
      <Icon size={14} className="text-muted-foreground" />
      <span className="flex-1">{option.label}</span>
      {option.summary && (
        <span className="max-w-24 truncate text-[11px] text-muted-foreground">
          {option.summary}
        </span>
      )}
      <ChevronRight size={13} className="text-muted-foreground/60" />
    </DropdownMenuItem>
  )
}

function MobileFilterRoot({
  options,
  hasAnyFilter,
  onSelect,
  onClear,
}: {
  options: RootOption[]
  hasAnyFilter: boolean
  onSelect: (view: RootOption['view']) => void
  onClear: () => void
}) {
  const { t } = useTranslation()
  return (
    <>
      <DropdownMenuLabel className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
        {t('transactions.filtersBar.filterBy')}
      </DropdownMenuLabel>
      {options.map((option) => (
        <MobileRootOption key={option.view} option={option} onSelect={onSelect} />
      ))}
      {hasAnyFilter && <MobileClearFilters onClear={onClear} />}
    </>
  )
}

function MobileClearFilters({ onClear }: { onClear: () => void }) {
  const { t } = useTranslation()
  return (
    <>
      <DropdownMenuSeparator />
      <DropdownMenuItem
        onSelect={onClear}
        className="gap-2 text-[12.5px] text-muted-foreground"
      >
        <X size={13} />
        {t('transactions.clearFilters')}
      </DropdownMenuItem>
    </>
  )
}

function MobileDetailHeader({
  title,
  onBack,
}: {
  title: string
  onBack: () => void
}) {
  const { t } = useTranslation()
  return (
    <>
      <button
        type="button"
        onClick={onBack}
        className="flex w-full items-center gap-1 rounded-sm px-2 py-1.5 text-left text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        <ChevronLeft size={14} />
        {t('common.back')}
      </button>
      <DropdownMenuLabel className="px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">
        {title}
      </DropdownMenuLabel>
    </>
  )
}

function MobileAccountOption({
  account,
  checked,
  onChange,
}: {
  account: Account
  checked: boolean
  onChange: () => void
}) {
  return (
    <DropdownMenuCheckboxItem
      checked={checked}
      onSelect={(event) => {
        event.preventDefault()
        onChange()
      }}
      className="gap-2 py-2 text-[13px]"
    >
      <span className="min-w-0 flex-1 truncate text-left">
        {getAccountName(account)}
      </span>
      {account.currency && (
        <span className="text-[10.5px] uppercase tracking-wide text-muted-foreground/70">
          {account.currency}
        </span>
      )}
    </DropdownMenuCheckboxItem>
  )
}

function MobileAccountView({
  accounts,
  selectedIds,
  onChange,
}: {
  accounts: Account[]
  selectedIds: string[]
  onChange: (value: string[]) => void
}) {
  const { t } = useTranslation()
  if (accounts.length === 0) {
    return <MobileEmptyOptions />
  }
  return (
    <>
      {accounts.map((account) => (
        <MobileAccountOption
          key={account.id}
          account={account}
          checked={selectedIds.includes(account.id)}
          onChange={() => onChange(toggleSelection(selectedIds, account.id))}
        />
      ))}
      {selectedIds.length > 0 && (
        <DropdownMenuItem
          onSelect={(event) => {
            event.preventDefault()
            onChange([])
          }}
          className="mt-1 gap-2 border-t border-border/60 text-xs text-muted-foreground"
        >
          <X size={12} />
          {t('transactions.filtersBar.clearSelection')}
        </DropdownMenuItem>
      )}
    </>
  )
}

function MobileEmptyOptions() {
  const { t } = useTranslation()
  return (
    <div className="px-2 py-3 text-center text-xs text-muted-foreground">
      {t('transactions.filtersBar.noOptions')}
    </div>
  )
}

function MobileSelectionView({
  options,
  selectedValue,
  onChange,
}: {
  options: SelectionOption[]
  selectedValue: string
  onChange: (value: string) => void
}) {
  return options.map((option) => (
    <DropdownMenuItem
      key={option.value || 'all'}
      onSelect={(event) => {
        event.preventDefault()
        onChange(option.value)
      }}
      className={cn(
        'gap-2 py-2 text-[13px]',
        selectedValue === option.value && 'bg-primary/5',
      )}
    >
      <span className="min-w-0 flex-1 truncate">{option.label}</span>
      {selectedValue === option.value && <Check size={13} className="text-primary" />}
    </DropdownMenuItem>
  ))
}

function MobileDateView({
  from,
  to,
  presets,
  onChange,
  onOpenCustomRange,
}: {
  from: string
  to: string
  presets: MobileDatePreset[]
  onChange: (from: string, to: string) => void
  onOpenCustomRange: () => void
}) {
  const { t } = useTranslation()
  const options = [
    { value: '|', label: t('transactions.all') },
    ...presets.map((preset) => ({
      value: `${preset.from}|${preset.to}`,
      label: preset.label,
    })),
  ]
  return (
    <>
      <MobileSelectionView
        options={options}
        selectedValue={`${from}|${to}`}
        onChange={(value) => onChange(...splitDateRange(value))}
      />
      <DropdownMenuSeparator />
      <DropdownMenuItem
        onSelect={onOpenCustomRange}
        className="justify-between py-2 text-[13px]"
      >
        {t('transactions.filtersBar.customRange')}
        <ChevronRight size={13} className="text-muted-foreground/60" />
      </DropdownMenuItem>
    </>
  )
}

function splitDateRange(value: string): [string, string] {
  const [from, to] = value.split('|')
  return [from, to]
}

function MobileAmountField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">
      {label}
      <input
        type="number"
        inputMode="decimal"
        min={0}
        step="0.01"
        placeholder="0.00"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 h-9 w-full rounded-md border border-border bg-card px-2 text-[13px] font-normal tracking-normal text-foreground outline-none focus:border-primary/60"
      />
    </label>
  )
}

function MobileAmountView({
  minAmount,
  maxAmount,
  setMinAmount,
  setMaxAmount,
  onReset,
  onApply,
}: {
  minAmount: string
  maxAmount: string
  setMinAmount: (value: string) => void
  setMaxAmount: (value: string) => void
  onReset: () => void
  onApply: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="p-2">
      <div className="grid grid-cols-2 gap-2">
        <MobileAmountField
          label={t('transactions.filtersBar.amountMinLabel')}
          value={minAmount}
          onChange={setMinAmount}
        />
        <MobileAmountField
          label={t('transactions.filtersBar.amountMaxLabel')}
          value={maxAmount}
          onChange={setMaxAmount}
        />
      </div>
      <p className="mt-2 text-[10.5px] leading-snug text-muted-foreground/80">
        {t('transactions.filtersBar.amountHint')}
      </p>
      <div className="mt-2 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onReset}
          className="text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          {t('transactions.filtersBar.reset')}
        </button>
        <Button
          type="button"
          size="sm"
          disabled={!minAmount && !maxAmount}
          onClick={onApply}
        >
          {t('transactions.filtersBar.apply')}
        </Button>
      </div>
    </div>
  )
}

function buildRootOptions(
  labels: Record<Exclude<MobileFilterView, 'root'>, string>,
  summaries: MobileTransactionsFilterMenuProps['summaries'],
): RootOption[] {
  return [
    { view: 'account', icon: Wallet, label: labels.account, summary: summaries.account },
    { view: 'category', icon: Tag, label: labels.category, summary: summaries.category },
    { view: 'payee', icon: Store, label: labels.payee, summary: summaries.payee },
    { view: 'group', icon: Users, label: labels.group, summary: summaries.group },
    { view: 'type', icon: ArrowUpDown, label: labels.type, summary: summaries.type },
    { view: 'status', icon: ListChecks, label: labels.status, summary: summaries.status },
    { view: 'ignored', icon: EyeClosed, label: labels.ignored, summary: summaries.ignored },
    { view: 'date', icon: CalendarIcon, label: labels.date, summary: summaries.date },
    { view: 'amount', icon: Coins, label: labels.amount, summary: summaries.amount },
  ]
}

function buildLabels(
  t: ReturnType<typeof useTranslation>['t'],
): Record<Exclude<MobileFilterView, 'root'>, string> {
  return {
    account: t('transactions.account'),
    category: t('transactions.category'),
    payee: t('payees.payee'),
    group: t('splitGroups.group'),
    type: t('transactions.type'),
    status: t('transactions.status'),
    ignored: t('transactions.ignoredRows'),
    date: t('transactions.filtersBar.date'),
    amount: t('transactions.filtersBar.amount'),
  }
}

function MobileFilterDetail({
  menu,
  labels,
}: {
  menu: MobileTransactionsFilterMenuProps
  labels: Record<Exclude<MobileFilterView, 'root'>, string>
}) {
  const { t } = useTranslation()
  const allOption = { value: '', label: t('transactions.all') }
  if (menu.view === 'account') {
    return <MobileAccountView accounts={menu.accounts} selectedIds={menu.accountIds} onChange={menu.onAccountIdsChange} />
  }
  if (menu.view === 'category') {
    return <CategoryFilterContent categoryIds={menu.categoryIds} onCategoryIdsChange={menu.onCategoryIdsChange} filterUncategorized={menu.uncategorized} onUncategorizedChange={menu.onUncategorizedChange} categories={menu.categories} groups={menu.categoryGroups} onKeepOpen={() => undefined} />
  }
  if (menu.view === 'payee') {
    return <MobileSelectionView options={[allOption, ...menu.payees.map(({ id, name }) => ({ value: id, label: name }))]} selectedValue={menu.payeeId} onChange={menu.onPayeeChange} />
  }
  if (menu.view === 'group') {
    return <MobileSelectionView options={[allOption, ...menu.groups.map(({ id, name }) => ({ value: id, label: name }))]} selectedValue={menu.groupId} onChange={menu.onGroupIdChange} />
  }
  if (menu.view === 'type') {
    const options = [allOption, { value: 'credit', label: t('transactions.income') }, { value: 'debit', label: t('transactions.expense') }]
    return <MobileSelectionView options={options} selectedValue={menu.type} onChange={menu.onTypeChange} />
  }
  if (menu.view === 'status') {
    const options = [allOption, { value: 'pending', label: t('transactions.statusPending') }, { value: 'posted', label: t('transactions.statusPosted') }]
    return <MobileSelectionView options={options} selectedValue={menu.status} onChange={menu.onStatusChange} />
  }
  if (menu.view === 'ignored') {
    const options = [
      { value: 'show', label: t('transactions.ignoredShow') },
      { value: 'hide', label: t('transactions.ignoredHide') },
    ]
    return <MobileSelectionView options={options} selectedValue={menu.hideIgnored ? 'hide' : 'show'} onChange={(value) => menu.onHideIgnoredChange(value === 'hide')} />
  }
  if (menu.view === 'date') {
    return <MobileDateView from={menu.from} to={menu.to} presets={menu.datePresets} onChange={menu.onDateRangeChange} onOpenCustomRange={menu.onOpenCustomRange} />
  }
  if (menu.view === 'amount') {
    return <MobileAmountView minAmount={menu.minAmount} maxAmount={menu.maxAmount} setMinAmount={menu.setMinAmount} setMaxAmount={menu.setMaxAmount} onReset={() => resetAmount(menu)} onApply={menu.onApplyAmountRange} />
  }
  return <MobileFilterRoot options={buildRootOptions(labels, menu.summaries)} hasAnyFilter={menu.hasAnyFilter} onSelect={(view) => openDetail(menu, view)} onClear={() => clearAll(menu)} />
}

function openDetail(
  menu: MobileTransactionsFilterMenuProps,
  view: Exclude<MobileFilterView, 'root'>,
) {
  if (view === 'amount') {
    menu.setMinAmount(menu.appliedMinAmount)
    menu.setMaxAmount(menu.appliedMaxAmount)
  }
  menu.setView(view)
}

function resetAmount(menu: MobileTransactionsFilterMenuProps) {
  menu.setMinAmount('')
  menu.setMaxAmount('')
  menu.onAmountRangeChange('', '')
}

function clearAll(menu: MobileTransactionsFilterMenuProps) {
  menu.onClearAll()
  menu.setMenuOpen(false)
}

/**
 * Keeps mobile transaction filters inside one navigable dropdown.
 * @example <MobileTransactionsFilterMenu {...menuProps} />
 */
export function MobileTransactionsFilterMenu(
  menu: MobileTransactionsFilterMenuProps,
) {
  const { t } = useTranslation()
  const labels = buildLabels(t)
  return (
    <div className="sm:hidden">
      {menu.view !== 'root' && (
        <MobileDetailHeader
          title={labels[menu.view]}
          onBack={() => menu.setView('root')}
        />
      )}
      <MobileFilterDetail menu={menu} labels={labels} />
    </div>
  )
}
