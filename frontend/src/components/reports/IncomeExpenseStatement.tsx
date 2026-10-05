import { Fragment, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  AlertCircle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  ChevronRight,
  SlidersHorizontal,
  TextSearch,
} from 'lucide-react'
import { reports } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { formatCurrency } from '@/lib/format'
import { monthLabel, monthRange } from '@/lib/month-utils'
import { cn } from '@/lib/utils'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { CategoryIcon } from '@/components/category-icon'
import { MonthStepper } from '@/components/month-stepper'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { TransactionDrillDown, type DrillDownFilter } from '@/components/transaction-drill-down'
import {
  STATEMENT_COLUMN_OPTIONS,
  sortStatementRows,
  statementChange,
  type StatementSection,
  type StatementSettings,
} from '@/lib/category-statement'
import type { CategoryStatementRow } from '@/types'


function capitalize(text: string) {
  return text.replace(/^\p{L}/u, (c) => c.toUpperCase())
}

function ChangeBadge({ change }: { change: { percent: number; good: boolean } }) {
  const rounded = Math.round(change.percent)
  const Arrow = rounded > 0 ? ArrowUpRight : rounded < 0 ? ArrowDownRight : ArrowRight
  const tone =
    rounded === 0
      ? 'text-muted-foreground'
      : change.good
        ? 'text-emerald-600 dark:text-emerald-400'
        : 'text-rose-600 dark:text-rose-400'
  const label = Math.abs(rounded) > 999 ? `${rounded > 0 ? '>' : '<-'}999%` : `${rounded}%`
  return (
    <span className={cn('inline-flex items-center gap-0.5 text-xs font-medium tabular-nums', tone)}>
      <Arrow size={13} aria-hidden="true" />
      {label}
    </span>
  )
}

interface StatementControlsProps {
  month: string
  onMonthChange: (month: string) => void
  settings: StatementSettings
  onSettingsChange: (settings: StatementSettings) => void
}

/** Month stepper and options popover, rendered in the page header like the other tabs' filters. */
export function StatementControls({ month, onMonthChange, settings, onSettingsChange }: StatementControlsProps) {
  const { t, i18n } = useTranslation()
  // Month names follow the app language, like the other month steppers.
  const dateLocale = i18n.resolvedLanguage ?? i18n.language
  return (
    <div className="flex items-center gap-2">
      <MonthStepper
        value={month}
        onChange={onMonthChange}
        locale={dateLocale}
        prevLabel={t('common.previous')}
        nextLabel={t('common.next')}
      />
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={t('reports.statementSettings')}
            title={t('reports.statementSettings')}
            className="h-8 w-8 shrink-0 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:text-foreground transition-colors"
          >
            <SlidersHorizontal size={16} />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-64 space-y-4">
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold mb-2">{t('reports.statementSortBy')}</legend>
            {(
              [
                ['default', 'reports.statementSortDefault'],
                ['asc', 'reports.statementSortAsc'],
                ['desc', 'reports.statementSortDesc'],
              ] as const
            ).map(([value, labelKey]) => (
              <label key={value} className="flex items-center gap-2.5 text-sm cursor-pointer">
                <input
                  type="radio"
                  name="statement-sort"
                  value={value}
                  checked={settings.sort === value}
                  onChange={() => onSettingsChange({ ...settings, sort: value })}
                  className="h-4 w-4 accent-primary"
                />
                {t(labelKey)}
              </label>
            ))}
          </fieldset>
          <div>
            <p id="statement-columns" className="text-sm font-semibold mb-2">{t('reports.statementColumns')}</p>
            <div role="group" aria-labelledby="statement-columns" className="flex rounded-lg border border-border overflow-hidden">
              {STATEMENT_COLUMN_OPTIONS.map((n) => (
                <button
                  key={n}
                  type="button"
                  aria-pressed={settings.columns === n}
                  onClick={() => onSettingsChange({ ...settings, columns: n })}
                  className={cn(
                    'flex-1 py-1.5 text-xs font-semibold transition-colors',
                    settings.columns === n
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/50',
                  )}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span id="statement-show-change" className="text-sm">{t('reports.statementShowChange')}</span>
            <Switch
              checked={settings.showChange}
              onCheckedChange={(checked) => onSettingsChange({ ...settings, showChange: checked })}
              aria-labelledby="statement-show-change"
            />
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}

interface IncomeExpenseStatementProps {
  accountIds?: string[]
  /** Selected month, "YYYY-MM": the rightmost column. */
  month: string
  settings: StatementSettings
}

export function IncomeExpenseStatement({ accountIds, month, settings }: IncomeExpenseStatementProps) {
  const { t, i18n } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = i18n.resolvedLanguage ?? i18n.language
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [drillDown, setDrillDown] = useState<DrillDownFilter | null>(null)

  // One extra month so the oldest visible column still has a change to show.
  const fetchMonths = settings.columns + 1
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['reports', 'category_statement', month, fetchMonths, accountIds ?? null],
    queryFn: () => reports.categoryStatement(month, fetchMonths, accountIds),
    placeholderData: keepPreviousData,
  })

  const currency = data?.currency ?? user?.preferences?.currency_display ?? 'USD'
  // Oldest month on the left, the selected one on the right. Indexes point
  // into the response, which lists months newest first.
  const columnIndexes = Array.from(
    { length: Math.min(settings.columns, data?.months.length ?? 0) },
    (_, i) => i,
  ).reverse()

  // The selected month is the rightmost column, so start scrolled to it when
  // the columns don't fit (phones, or many columns).
  const scrollRef = useRef<HTMLDivElement>(null)
  const selectedMonth = data?.months[0]
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollLeft = el.scrollWidth
  }, [selectedMonth, settings.columns])

  const toggleGroup = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const rowLabel = (row: CategoryStatementRow) =>
    row.kind === 'uncategorized'
      ? t(row.txn_type === 'credit' ? 'reports.statementUncategorizedIncome' : 'reports.statementUncategorizedExpense')
      : (row.label ?? '')

  const openDrillDown = (row: CategoryStatementRow, ym: string) => {
    const { from, to } = monthRange(ym)
    setDrillDown({
      title: `${rowLabel(row)} · ${capitalize(monthLabel(ym, dateLocale))}`,
      ...(row.kind === 'uncategorized'
        ? { uncategorized: true, type: row.txn_type ?? undefined }
        : { category_ids: row.category_ids }),
      account_ids: accountIds,
      from,
      to,
      realizedOnly: true,
      netTotal: true,
    })
  }

  const renderValues = (
    values: number[],
    section: StatementSection,
    opts: { strong?: boolean; row?: CategoryStatementRow; muted?: boolean } = {},
  ) =>
    columnIndexes.map((i) => {
      const ym = data!.months[i]
      const value = values[i] ?? 0
      const change = settings.showChange ? statementChange(value, values[i + 1], section) : null
      const isZero = Math.abs(value) < 0.005
      return (
        <td key={ym} className="px-3 py-2.5 text-right whitespace-nowrap">
          <div className="flex items-center justify-end gap-2.5">
            {opts.row && !isZero && (
              <button
                type="button"
                onClick={() => openDrillDown(opts.row!, ym)}
                aria-label={t('reports.statementViewTransactions')}
                title={t('reports.statementViewTransactions')}
                className="text-muted-foreground hover:text-foreground transition-colors"
              >
                <TextSearch size={15} />
              </button>
            )}
            {change && <ChangeBadge change={change} />}
            <span
              className={cn(
                'tabular-nums',
                opts.strong ? 'font-semibold' : 'font-medium',
                isZero || opts.muted ? 'text-muted-foreground' : 'text-foreground',
              )}
            >
              {mask(formatCurrency(value, currency, locale))}
            </span>
          </div>
        </td>
      )
    })

  const renderRow = (row: CategoryStatementRow, section: StatementSection, depth = 0): ReactNode => {
    const isGroup = row.kind === 'group' && row.children.length > 0
    const isOpen = expanded.has(`${section}:${row.key}`)
    const label = rowLabel(row)
    return (
      <Fragment key={`${section}:${row.key}`}>
        <tr className="border-t border-border hover:bg-muted/30 transition-colors">
          <th
            scope="row"
            className="sticky left-0 z-[1] bg-card px-3 sm:px-4 py-2 text-left font-normal max-w-[10.5rem] sm:max-w-xs"
          >
            <div className={cn('flex items-center gap-2.5 min-w-0', depth > 0 && 'pl-9')}>
              {isGroup ? (
                <button
                  type="button"
                  onClick={() => toggleGroup(`${section}:${row.key}`)}
                  aria-expanded={isOpen}
                  aria-label={t('reports.statementToggleGroup', { name: label })}
                  className="-ml-1 text-muted-foreground hover:text-foreground transition-colors"
                >
                  <ChevronRight size={16} className={cn('transition-transform', isOpen && 'rotate-90')} />
                </button>
              ) : (
                depth === 0 && <span className="w-[11px] shrink-0" aria-hidden="true" />
              )}
              <CategoryIcon
                icon={row.kind === 'uncategorized' ? 'circle-help' : row.icon}
                color={row.kind === 'uncategorized' ? '#9CA3AF' : row.color}
                size={depth > 0 ? 'sm' : 'md'}
              />
              <span className="truncate text-foreground" title={label}>{label}</span>
            </div>
          </th>
          {renderValues(row.values, section, { row })}
        </tr>
        {isGroup && isOpen && row.children.map((child) => renderRow(child, section, depth + 1))}
      </Fragment>
    )
  }

  const renderSection = (section: 'income' | 'expenses') => {
    const block = data?.[section]
    if (!block) return null
    const title = t(section === 'income' ? 'reports.income' : 'reports.expenses')
    const totalLabel = t(section === 'income' ? 'reports.statementIncomeTotal' : 'reports.statementExpenseTotal')
    return (
      <tbody>
        <tr className="bg-muted">
          <th scope="col" className="sticky left-0 z-[1] bg-muted px-3 sm:px-4 py-2 text-left text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {title}
          </th>
          {columnIndexes.map((i) => (
            <th key={data!.months[i]} scope="col" className="px-3 py-2 text-right text-xs font-semibold text-foreground whitespace-nowrap">
              {capitalize(monthLabel(data!.months[i], dateLocale))}
            </th>
          ))}
        </tr>
        <tr className="border-t border-border">
          <th scope="row" className="sticky left-0 z-[1] bg-card px-3 sm:px-4 py-3 text-left font-semibold text-foreground">
            {totalLabel}
          </th>
          {renderValues(block.totals, section, { strong: true })}
        </tr>
        {sortStatementRows(block.rows, settings.sort).map((row) => renderRow(row, section))}
      </tbody>
    )
  }

  const netValues = data
    ? data.months.map((_, i) => (data.income.totals[i] ?? 0) + (data.expenses.totals[i] ?? 0))
    : []
  const isEmpty = !!data && data.income.rows.length === 0 && data.expenses.rows.length === 0

  return (
    <div>
      {isError ? (
        <div className="flex items-center justify-between gap-3 bg-rose-50 dark:bg-rose-500/10 border border-rose-200 dark:border-rose-500/30 rounded-lg px-4 py-2.5">
          <div className="flex items-center gap-2.5 min-w-0">
            <AlertCircle size={16} className="shrink-0 text-rose-600 dark:text-rose-400" />
            <span className="text-sm text-rose-900 dark:text-rose-200 truncate">
              {extractApiError(error, t('reports.loadError'))}
            </span>
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            className="shrink-0 text-sm font-semibold text-rose-600 dark:text-rose-400 hover:underline"
          >
            {t('common.retry')}
          </button>
        </div>
      ) : isLoading ? (
        <div className="bg-card rounded-xl border border-border shadow-sm p-4 space-y-3">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : (
        <div ref={scrollRef} className="bg-card rounded-xl border border-border shadow-sm overflow-x-auto">
          <table className="w-full text-sm">
            {renderSection('income')}
            {renderSection('expenses')}
            <tfoot>
              <tr className="border-t-2 border-border">
                <th scope="row" className="sticky left-0 z-[1] bg-card px-3 sm:px-4 py-3 text-left font-semibold text-foreground">
                  {t('reports.netIncome')}
                </th>
                {renderValues(netValues, 'net', { strong: true })}
              </tr>
            </tfoot>
          </table>
          {isEmpty && (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">{t('reports.noData')}</p>
          )}
        </div>
      )}

      <TransactionDrillDown filter={drillDown} onClose={() => setDrillDown(null)} />
    </div>
  )
}
