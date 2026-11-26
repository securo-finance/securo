import { useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertCircle, ArrowRightLeft, CalendarIcon, ChevronDown, ChevronUp, Copy as CopyIcon, Repeat, Sparkles } from 'lucide-react'
import { format } from 'date-fns'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { monthLabel, monthRange, shiftMonth } from '@/lib/month-utils'
import { categories as categoriesApi, categoryGroups as groupsApi, budgets as budgetsApi, dashboard } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { MonthPicker } from '@/components/ui/monthpicker'
import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/page-header'
import { BudgetSpendingProgress } from '@/components/budget-spending-progress'
import { BudgetCopyDialog } from '@/components/budget-copy-dialog'
import { TransactionDrillDown } from '@/components/transaction-drill-down'
import { CategoryIcon } from '@/components/category-icon'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useCollectionFilter } from '@/contexts/collection-filter-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { useSidebarState } from '@/contexts/sidebar-state-context'
import { cn } from '@/lib/utils'
import { useEffectiveTimezone } from '@/hooks/use-timezone'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { budgetDisplayAmount, parseBudgetDraft, planBudgetChange, type BudgetOperation } from '@/lib/budget-settings-utils'
import { todayInTimezone } from '@/lib/date-utils'
import { formatAmountInput, formatCurrency, ltrAmount } from '@/lib/format'
import type { BudgetSuggestion, Category } from '@/types'

/** Months of history shown per category; suggestion amounts come from the backend average. */
const HISTORY_MONTHS = 6

const STEP_BUTTON = 'h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:border-border hover:text-foreground transition-all text-base'

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}

function SectionHeader({ title, description, action }: { title: string; description?: string; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {description && <p className="text-xs text-muted-foreground mt-0.5">{description}</p>}
      </div>
      {action}
    </div>
  )
}

/** Six small bars, oldest first, so a category's usual spend is visible at a glance. */
function SpendHistory({ values, labels }: { values: number[]; labels: string[] }) {
  const max = Math.max(...values, 1)
  return (
    <div className="hidden md:flex md:col-start-2 md:row-start-1 h-6 items-end gap-0.5" aria-hidden>
      {values.map((value, index) => (
        <span
          key={index}
          title={labels[index]}
          className={`w-1.5 rounded-sm ${index === values.length - 1 ? 'bg-muted-foreground/60' : 'bg-muted-foreground/25'}`}
          style={{ height: `${Math.max(8, (value / max) * 100)}%` }}
        />
      ))}
    </div>
  )
}

export default function BudgetsPage() {
  const { t, i18n } = useTranslation()
  const id = useId()
  const { mask, privacyMode } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const { activeAccountIds } = useCollectionFilter()
  const noAccounts = activeAccountIds !== null && activeAccountIds.length === 0
  const { collapsed: sidebarCollapsed } = useSidebarState()
  const timeZone = useEffectiveTimezone()
  const today = todayInTimezone(timeZone)
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedMonth = searchParams.get('month')
  const validRequestedMonth = requestedMonth && /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
    ? requestedMonth
    : null
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const [monthOverride, setMonthOverride] = useState<string | null>(() => validRequestedMonth)
  const selectedMonth = monthOverride ?? today.slice(0, 7)
  const [monthCalOpen, setMonthCalOpen] = useState(false)
  const [drillDownCategoryId, setDrillDownCategoryId] = useState<string | null>(null)
  const [focusedCategoryId, setFocusedCategoryId] = useState<string | null>(null)
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [copyOpen, setCopyOpen] = useState(false)
  const [forecastOpen, setForecastOpen] = useState(false)
  const [forecastMonths, setForecastMonths] = useState<3 | 6 | 12>(6)
  const [suggestMonths, setSuggestMonths] = useState(3)
  const [suggestRecurring, setSuggestRecurring] = useState(false)
  const [suggestEdits, setSuggestEdits] = useState<Record<string, { checked: boolean; amount: string }>>({})
  const uiLocale = i18n.resolvedLanguage ?? i18n.language
  const dateFnsLocale = resolveDateFnsLocale(uiLocale)
  const monthParam = `${selectedMonth}-01`
  const monthTitle = monthLabel(selectedMonth, uiLocale).replace(/^\p{L}/u, (letter) => letter.toLocaleUpperCase(uiLocale))
  const selectMonth = (nextMonth: string) => {
    setMonthOverride(nextMonth)
    const next = new URLSearchParams(searchParams)
    next.set('month', nextMonth)
    setSearchParams(next)
  }
  // Drafts are per month: switching month starts from what is saved there.
  type DraftState = {
    month: string
    drafts: Record<string, string>
    recurring: Record<string, boolean>
    rollover: Record<string, boolean>
    resetRollover: Record<string, boolean>
  }
  const emptyDrafts = (): DraftState => ({
    month: selectedMonth,
    drafts: {},
    recurring: {},
    rollover: {},
    resetRollover: {},
  })
  const [draftState, setDraftState] = useState<DraftState>(emptyDrafts)
  const current = draftState.month === selectedMonth ? draftState : emptyDrafts()
  const drafts = current.drafts

  const fmt = (amount: number) => mask(formatCurrency(amount, userCurrency, locale))
  const fmtSigned = (amount: number) => {
    const formatted = formatCurrency(Math.abs(amount), userCurrency, locale)
    const signed = amount > 0 ? `+${formatted}` : amount < 0 ? `-${formatted}` : formatted
    return mask(ltrAmount(signed))
  }

  const { data: budgetsList, isLoading: budgetsLoading, isFetching: budgetsFetching, isError: budgetsError, error: budgetsLoadError, refetch: refetchBudgets } = useQuery({
    queryKey: ['budgets', selectedMonth],
    queryFn: () => budgetsApi.list(monthParam),
  })
  const { data: spending, isPending: spendingPending, isError: spendingError, refetch: refetchSpending } = useQuery({
    queryKey: ['dashboard', 'spending', selectedMonth, activeAccountIds],
    queryFn: () => dashboard.spendingByCategory(monthParam, activeAccountIds ?? undefined),
    enabled: !noAccounts,
  })
  const spendingAvailable = noAccounts || (!spendingPending && !spendingError)
  const spendingByCategory = new Map(
    (noAccounts ? [] : spending ?? []).map((item) => [item.category_id, item.projected_total]),
  )
  const { data: budgetSeries } = useQuery({
    queryKey: ['budgets', 'series'],
    queryFn: () => budgetsApi.list(),
  })
  const { data: categoriesList, isLoading: categoriesLoading, isError: categoriesError, error: categoriesLoadError, refetch: refetchCategories } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.listIncludingHidden,
  })
  const { data: groupsList, isLoading: groupsLoading, isError: groupsError, error: groupsLoadError, refetch: refetchGroups } = useQuery({
    queryKey: ['category-groups'],
    queryFn: groupsApi.listIncludingHidden,
  })
  const { data: suggestionData, isPending: suggestionsPending, isError: suggestionsError } = useQuery({
    queryKey: ['budgets', 'suggestions', monthParam, suggestMonths],
    queryFn: () => budgetsApi.suggestions(monthParam, suggestMonths),
  })
  const historyMonthKeys = useMemo(
    () => Array.from({ length: HISTORY_MONTHS }, (_, index) => shiftMonth(selectedMonth, -(index + 1))),
    [selectedMonth],
  )
  // Same query as the budget overview's history, so the two pages share the cache.
  const { data: history } = useQuery({
    queryKey: ['budgets', 'history', selectedMonth],
    queryFn: async () => Promise.all(historyMonthKeys.map((key) => budgetsApi.comparison(`${key}-01`))),
  })
  const { data: comparisonData } = useQuery({
    queryKey: ['budgets', 'comparison', selectedMonth],
    queryFn: () => budgetsApi.comparison(monthParam),
  })
  const {
    data: rolloverSummary,
    isPending: rolloverSummaryPending,
    isError: rolloverSummaryError,
    refetch: refetchRolloverSummary,
  } = useQuery({
    queryKey: ['budgets', 'rollover-summary', selectedMonth],
    queryFn: () => budgetsApi.rolloverSummary(monthParam),
    enabled: forecastOpen,
  })
  const {
    data: forecastData,
    isPending: forecastPending,
    isError: forecastError,
    refetch: refetchForecast,
  } = useQuery({
    queryKey: ['budgets', 'forecast', selectedMonth, forecastMonths],
    queryFn: () => budgetsApi.forecast(monthParam, forecastMonths),
    enabled: forecastOpen,
  })

  const budgetByCategory = useMemo(
    () => new Map((budgetsList ?? []).map((budget) => [budget.category_id, budget])),
    [budgetsList],
  )
  const comparisonByCategory = useMemo(
    () => new Map((comparisonData ?? []).map((row) => [row.category_id, row])),
    [comparisonData],
  )
  const savedAmount = (categoryId: string) => {
    const budget = budgetByCategory.get(categoryId)
    return budget ? budgetDisplayAmount(budget) : 0
  }
  const draftFor = (categoryId: string) => drafts[categoryId] ?? (savedAmount(categoryId) > 0 ? formatAmountInput(savedAmount(categoryId), locale) : '')
  // New budgets repeat every month unless switched off, like the old add dialog's default.
  const recurringFor = (categoryId: string) => current.recurring[categoryId] ?? budgetByCategory.get(categoryId)?.is_recurring ?? true
  const rolloverFor = (categoryId: string) => current.rollover[categoryId] ?? budgetByCategory.get(categoryId)?.rollover ?? false
  const resetRolloverFor = (categoryId: string) => current.resetRollover[categoryId] ?? false
  const parsedDraftFor = (categoryId: string) => parseBudgetDraft(draftFor(categoryId), locale)
  const planFor = (categoryId: string) => {
    const amount = parsedDraftFor(categoryId)
    if (amount === null) return []
    return planBudgetChange({
      categoryId,
      amount,
      recurring: recurringFor(categoryId),
      rollover: rolloverFor(categoryId),
      resetRollover: resetRolloverFor(categoryId),
      current: budgetByCategory.get(categoryId),
      series: (budgetSeries ?? []).filter((budget) => budget.category_id === categoryId),
      month: monthParam,
    })
  }
  // Include hidden categories/groups because existing budgets still count in reports.
  // Transfers and ignored categories never get a budget.
  const sections = useMemo(() => {
    const budgetable = (category: Category) => !category.treat_as_transfer && !category.is_ignored
    const grouped = (groupsList ?? [])
      .map((group) => ({ id: group.id, name: group.name, categories: group.categories.filter(budgetable) }))
      .filter((group) => group.categories.length > 0)
    const groupedCategoryIds = new Set(grouped.flatMap((group) => group.categories.map((category) => category.id)))
    const ungrouped = (categoriesList ?? []).filter((category) => !groupedCategoryIds.has(category.id) && budgetable(category))
    return ungrouped.length ? [...grouped, { id: 'ungrouped', name: t('budgets.otherCategories'), categories: ungrouped }] : grouped
  }, [groupsList, categoriesList, t])
  const allCategories = sections.flatMap((section) => section.categories)

  // Only plan categories the user edited in this month. Merely opening a
  // month must never turn stored zero tombstones into delete operations.
  const dirtyCategoryIds = new Set([
    ...Object.keys(drafts),
    ...Object.keys(current.recurring),
    ...Object.keys(current.rollover),
    ...Object.keys(current.resetRollover),
  ])
  const operations = allCategories.flatMap((category) => dirtyCategoryIds.has(category.id) ? planFor(category.id) : [])
  const changedCategories = new Set(allCategories.filter((category) => dirtyCategoryIds.has(category.id) && planFor(category.id).length > 0).map((category) => category.id))
  const invalidCategories = new Set(allCategories.filter((category) => parsedDraftFor(category.id) === null).map((category) => category.id))
  const totalBudget = allCategories.reduce((sum, category) => sum + (parsedDraftFor(category.id) ?? 0), 0)
  const rowFor = (suggestion: BudgetSuggestion) =>
    suggestEdits[suggestion.category_id] ?? { checked: !suggestion.existing_budget, amount: String(suggestion.suggested_amount) }
  const suggestionByCategory = new Map((suggestionData?.suggestions ?? []).map((suggestion) => [suggestion.category_id, suggestion]))
  const suggestable = (suggestionData?.suggestions ?? []).filter((suggestion) => !suggestion.existing_budget)
  const batchItems = suggestable.filter((suggestion) => {
    const row = rowFor(suggestion)
    return row.checked && Number(row.amount) > 0
  })

  const updateDrafts = (change: (state: DraftState) => Partial<DraftState>) =>
    setDraftState((state) => {
      const base = state.month === selectedMonth ? state : emptyDrafts()
      return { ...base, ...change(base) }
    })
  const setDraft = (categoryId: string, value: string) =>
    updateDrafts((state) => ({ drafts: { ...state.drafts, [categoryId]: value } }))
  const toggleRecurring = (categoryId: string) =>
    updateDrafts((state) => ({ recurring: { ...state.recurring, [categoryId]: !recurringFor(categoryId) } }))
  const toggleRollover = (categoryId: string) =>
    updateDrafts((state) => ({ rollover: { ...state.rollover, [categoryId]: !rolloverFor(categoryId) } }))
  const toggleResetRollover = (categoryId: string) =>
    updateDrafts((state) => ({ resetRollover: { ...state.resetRollover, [categoryId]: !resetRolloverFor(categoryId) } }))

  const saveMutation = useMutation({
    mutationFn: async (planned: BudgetOperation[]) => {
      // Sequential: a delete must land before the create that replaces it.
      for (const operation of planned) {
        if (operation.type === 'create') await budgetsApi.create(operation.data)
        else if (operation.type === 'update') await budgetsApi.update(operation.id, operation.data)
        else await budgetsApi.delete(operation.id)
      }
    },
    onSuccess: () => {
      setDraftState(emptyDrafts())
      toast.success(t('budgets.saved'))
    },
    onError: (error: unknown) => toast.error(extractApiError(error, t('common.error'))),
    // Even a partly failed save changed some budgets; show what is stored now.
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['budgets'] }),
  })

  const batchMutation = useMutation({
    mutationFn: () => budgetsApi.createBatch({
      month: monthParam,
      is_recurring: suggestRecurring,
      items: batchItems.map((suggestion) => ({
        category_id: suggestion.category_id,
        amount: Number(rowFor(suggestion).amount),
      })),
    }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setSuggestOpen(false)
      toast.success(t('budgets.suggestCreated', { count: created.length }))
    },
    onError: (error: unknown) => toast.error(extractApiError(error, t('common.error'))),
  })

  const historyLabels = [...historyMonthKeys].reverse().map((key) => monthLabel(key, uiLocale))

  return (
    <div className={canWrite && operations.length > 0 ? 'pb-32' : undefined}>
      <PageHeader
        section={t('nav.groupSetup')}
        title={t('budgets.settingsTitle')}
        action={
          <div className="flex flex-wrap items-center gap-2">
            {canWrite && (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 h-8"
                // Without the month's budgets the dialog cannot tell which
                // categories are missing, so it would overstate the copy —
                // and a refetch still in flight is just as stale.
                disabled={budgetsLoading || budgetsFetching || budgetsError || !budgetsList}
                onClick={() => setCopyOpen(true)}
              >
                <CopyIcon size={13} /> {t('budgets.copyAction')}
              </Button>
            )}
            <div className="flex flex-wrap items-center gap-1">
              <button type="button" className={STEP_BUTTON} aria-label={t('budgets.previousMonth')} onClick={() => selectMonth(shiftMonth(selectedMonth, -1))}>‹</button>
              <Popover open={monthCalOpen} onOpenChange={setMonthCalOpen}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-2 border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground hover:bg-muted/50 transition-all cursor-pointer min-w-[180px]"
                  >
                    <CalendarIcon className="size-3.5 text-muted-foreground" />
                    {monthTitle}
                  </button>
                </PopoverTrigger>
                <PopoverContent align="center" className="w-auto p-0">
                  <MonthPicker
                    locale={dateFnsLocale}
                    selectedMonth={new Date(`${selectedMonth}-01T00:00:00`)}
                    onMonthSelect={(date) => {
                      if (!date) return
                      selectMonth(format(date, 'yyyy-MM'))
                      setMonthCalOpen(false)
                    }}
                  />
                </PopoverContent>
              </Popover>
              <button type="button" className={STEP_BUTTON} aria-label={t('budgets.nextMonth')} onClick={() => selectMonth(shiftMonth(selectedMonth, 1))}>›</button>
            </div>
          </div>
        }
      />

      <SectionCard>
        <SectionHeader
          title={t('budgets.monthBudgets', { month: monthTitle })}
          description={t('budgets.spendingDescription')}
          action={canWrite ? (
            <Button variant="outline" size="sm" className="gap-1.5 h-8" onClick={() => { setSuggestEdits({}); setSuggestOpen(true) }}>
              <Sparkles size={13} /> {t('budgets.suggest')}
            </Button>
          ) : undefined}
        />

        {canWrite && !budgetsFetching && budgetsList?.length === 0 && allCategories.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-muted/20 px-4 py-3 sm:px-5">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">{t('budgets.empty')}</p>
              <p className="text-xs text-muted-foreground">{t('budgets.copyEmptyHint')}</p>
            </div>
            <Button variant="outline" size="sm" className="gap-1.5 h-8" onClick={() => setCopyOpen(true)}>
              <CopyIcon size={13} /> {t('budgets.copyAction')}
            </Button>
          </div>
        )}
        {spendingError && !noAccounts && (
          <div role="alert" className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3 text-sm text-destructive">
            <span>{t('budgets.spendingError')}</span>
            <Button variant="outline" size="sm" onClick={() => { void refetchSpending() }}>{t('common.retry')}</Button>
          </div>
        )}
        {budgetsError || categoriesError || groupsError ? (
          <div role="alert" className="flex items-center justify-between gap-3 px-4 sm:px-5 py-4 text-sm text-rose-700 dark:text-rose-300">
            <span className="flex items-center gap-2"><AlertCircle size={16} />{extractApiError(budgetsLoadError ?? categoriesLoadError ?? groupsLoadError, t('common.error'))}</span>
            <Button variant="outline" size="sm" onClick={() => { void Promise.all([refetchBudgets(), refetchCategories(), refetchGroups()]) }}>{t('common.retry')}</Button>
          </div>
        ) : budgetsLoading || categoriesLoading || groupsLoading || !budgetsList || !categoriesList || !groupsList ? (
          <div className="p-5 space-y-3">{Array.from({ length: 5 }).map((_, index) => <Skeleton key={index} className="h-10 w-full" />)}</div>
        ) : allCategories.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-10">{t('budgets.noCategories')}</p>
        ) : (
          sections.map((section) => (
            <div key={section.id}>
              <p className="bg-muted/30 px-4 sm:px-5 py-2 text-xs font-medium text-muted-foreground border-b border-border">{section.name}</p>
              <div className="divide-y divide-border border-b border-border">
                {section.categories.map((category) => {
                  const draft = draftFor(category.id)
                  const suggestion = suggestionByCategory.get(category.id)
                  const values = history ? [...history].reverse().map((rows) => rows.find((row) => row.category_id === category.id)?.actual_amount ?? 0) : []
                  const changed = changedCategories.has(category.id)
                  const recurring = recurringFor(category.id)
                  const rollover = rolloverFor(category.id)
                  const resetPending = resetRolloverFor(category.id)
                  const spent = spendingByCategory.get(category.id) ?? 0
                  // Preview the valid draft limit + active carryover, just like the live total below.
                  // No edit is persisted until the user presses Save.
                  const limit = parsedDraftFor(category.id)
                  const savedCarryover = comparisonByCategory.get(category.id)?.carryover_amount ?? 0
                  const activeCarryover = rollover && limit !== null && limit > 0 && !resetPending ? savedCarryover : 0
                  const available = limit !== null ? limit + activeCarryover : null
                  const percentage = available !== null && available > 0 ? Math.round(spent / available * 1000) / 10 : 0
                  return (
                    <div key={category.id} className="grid grid-cols-[minmax(0,1fr)_auto_auto] md:grid-cols-[minmax(0,1fr)_auto_minmax(8rem,12rem)_auto_8rem] items-center gap-x-2 gap-y-2 md:gap-x-3 px-4 sm:px-5 py-2.5">
                      <div className="col-start-1 row-start-1 flex min-w-0 items-center gap-3">
                        <CategoryIcon icon={category.icon} color={category.color} size="md" />
                        <div className="min-w-0 flex-1">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.currentTarget.focus()
                              setDrillDownCategoryId(category.id)
                            }}
                            aria-label={t('dashboard.drillDownCategory', { category: category.name, month: monthLabel(selectedMonth, uiLocale) })}
                            className="max-w-full break-words text-start text-sm font-medium text-foreground hover:text-primary rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                          >
                            {category.name}
                          </button>
                          {category.is_hidden && <span className="block text-[11px] text-muted-foreground">{t('budgets.hiddenCategory')}</span>}
                          {suggestion && <p className="text-[11px] text-muted-foreground tabular-nums">{t('budgets.average', { amount: fmt(Number(suggestion.suggested_amount)) })}</p>}
                          {invalidCategories.has(category.id) && <p id={`budget-error-${category.id}`} role="alert" className="text-[11px] text-destructive">{t('budgets.invalidAmount')}</p>}
                        </div>
                      </div>
                      {values.length > 0 && (
                        <SpendHistory values={values} labels={values.map((value, index) => `${historyLabels[index]}: ${fmt(value)}`)} />
                      )}
                      <div className="col-span-3 row-start-2 min-w-0 ps-11 md:col-span-1 md:col-start-3 md:row-start-1 md:ps-0">
                        <p className="mb-1 text-xs text-muted-foreground">
                          {t('budgets.spending')}{' '}
                          <span className="text-sm font-normal tabular-nums text-foreground break-words">{spendingAvailable ? fmt(spent) : '—'}</span>
                        </p>
                        {spendingAvailable && available !== null && available > 0 && (
                          <BudgetSpendingProgress categoryName={category.name} percentage={percentage} budgetAmount={available} currency={userCurrency} showBudgetLabel={false} />
                        )}
                        {limit !== null && limit > 0 && (savedCarryover !== 0 || resetPending) && (
                          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground tabular-nums">
                            <span>
                              {t('budgets.carryoverBreakdown', {
                                base: fmt(limit),
                                carryover: fmtSigned(activeCarryover),
                                available: fmt(available ?? limit),
                              })}
                            </span>
                            {canWrite && savedCarryover !== 0 && (
                              <button
                                type="button"
                                aria-pressed={resetPending}
                                onClick={() => toggleResetRollover(category.id)}
                                className={`underline underline-offset-2 transition-colors ${resetPending ? 'text-primary font-medium' : 'text-muted-foreground hover:text-foreground'}`}
                              >
                                {resetPending ? t('budgets.resetCarryoverPending') : t('budgets.resetCarryover')}
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                      <div className="col-start-2 row-start-1 md:col-start-4 flex items-center gap-1">
                        <button
                          type="button"
                          aria-pressed={recurring}
                          aria-label={t('budgets.repeatEveryMonthFor', { category: category.name })}
                          title={t('budgets.repeatEveryMonth')}
                          disabled={!canWrite}
                          onClick={() => toggleRecurring(category.id)}
                          className={`p-1.5 rounded-md transition-colors disabled:opacity-60 ${recurring ? 'text-primary bg-primary/10 hover:bg-primary/15' : 'text-muted-foreground/60 hover:text-foreground hover:bg-muted'}`}
                        >
                          <Repeat size={13} />
                        </button>
                        <button
                          type="button"
                          aria-pressed={rollover}
                          aria-label={t('budgets.rolloverFor', { category: category.name })}
                          title={t('budgets.rolloverHint')}
                          disabled={!canWrite}
                          onClick={() => toggleRollover(category.id)}
                          className={`p-1.5 rounded-md transition-colors disabled:opacity-60 ${rollover ? 'text-primary bg-primary/10 hover:bg-primary/15' : 'text-muted-foreground/60 hover:text-foreground hover:bg-muted'}`}
                        >
                          <ArrowRightLeft size={13} />
                        </button>
                      </div>
                      <div className={`col-start-3 row-start-1 md:col-start-5 flex w-24 md:w-32 min-w-0 items-center rounded-lg border bg-card px-2.5 focus-within:ring-2 focus-within:ring-primary ${invalidCategories.has(category.id) ? 'border-destructive' : changed ? 'border-primary/60' : 'border-border'}`}>
                        <span className="text-xs text-muted-foreground">{formatCurrency(0, userCurrency, locale).replace(/[\d.,\s]/g, '')}</span>
                        <input
                          type="text"
                          inputMode="decimal"
                          aria-label={t('budgets.budgetFor', { category: category.name })}
                          aria-invalid={invalidCategories.has(category.id)}
                          aria-describedby={invalidCategories.has(category.id) ? `budget-error-${category.id}` : undefined}
                          placeholder="0"
                          value={privacyMode && focusedCategoryId !== category.id && draft ? mask(draft) : draft}
                          disabled={!canWrite}
                          onFocus={() => setFocusedCategoryId(category.id)}
                          onBlur={() => setFocusedCategoryId(null)}
                          onChange={(event) => setDraft(category.id, event.target.value)}
                          className="h-8 w-full min-w-0 bg-transparent text-end text-sm font-semibold tabular-nums text-foreground outline-none placeholder:text-muted-foreground/50 disabled:opacity-60"
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          ))
        )}
      </SectionCard>

      {/* Save bar: docked beneath the budget list when clean so it never occludes
          the forecast panel below, and fixed to the viewport bottom once edits are
          staged (matching the transactions bulk-action bar) so Save stays in reach
          while editing a long list. */}
      {canWrite && allCategories.length > 0 && !budgetsLoading && !categoriesLoading && !groupsLoading && !budgetsError && !categoriesError && !groupsError && budgetsList && budgetSeries && categoriesList && groupsList && (
        <div
          data-testid="budget-save-bar"
          className={cn(
            operations.length > 0
              ? [
                  'fixed bottom-0 start-0 end-0 z-40',
                  'transition-[left] duration-300 ease-in-out motion-reduce:transition-none',
                  sidebarCollapsed ? 'lg:start-16' : 'lg:start-60',
                ]
              : 'mt-4',
          )}
        >
          <div className={cn(operations.length > 0 && 'mx-auto max-w-7xl px-3 md:px-6 pb-4 md:pb-6')}>
            <div className={cn(
              'flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between bg-card border border-border rounded-2xl px-4 py-3',
              operations.length > 0 ? 'shadow-xl' : 'shadow-sm',
            )}>
              <div className="text-sm tabular-nums">
                <span className="font-semibold text-foreground">{t('budgets.total', { amount: fmt(totalBudget) })}</span>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Button variant="outline" size="sm" disabled={operations.length === 0 || saveMutation.isPending} onClick={() => setDraftState(emptyDrafts())}>
                  {t('common.cancel')}
                </Button>
                <Button size="sm" disabled={operations.length === 0 || invalidCategories.size > 0 || saveMutation.isPending} onClick={() => saveMutation.mutate(operations)}>
                  {t('common.save')}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="mt-6">
        <SectionCard>
          <div className={cn('px-4 sm:px-5 py-4 flex flex-wrap items-center justify-between gap-2', forecastOpen && 'border-b border-border')}>
            <button
              type="button"
              aria-expanded={forecastOpen}
              aria-controls="budget-rollover-forecast-panel"
              onClick={() => setForecastOpen((open) => !open)}
              className="flex flex-1 items-center justify-between gap-3 text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-md"
            >
              <div className="min-w-0">
                <p className="text-sm font-semibold text-foreground">{t('budgets.rolloverForecastTitle')}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {t('budgets.rolloverForecastDescription', { month: monthTitle })}
                </p>
              </div>
              {forecastOpen ? (
                <ChevronUp size={16} className="shrink-0 text-muted-foreground" />
              ) : (
                <ChevronDown size={16} className="shrink-0 text-muted-foreground" />
              )}
            </button>
          </div>

          {forecastOpen && (
            <div id="budget-rollover-forecast-panel" className="p-4 sm:p-5 space-y-6">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="text-xs font-medium text-muted-foreground">{t('budgets.forecastHorizon')}</span>
                <div className="inline-flex items-center gap-1" role="group" aria-label={t('budgets.forecastHorizon')}>
                  {([3, 6, 12] as const).map((months) => (
                    <Button
                      key={months}
                      type="button"
                      size="sm"
                      variant={forecastMonths === months ? 'default' : 'outline'}
                      aria-pressed={forecastMonths === months}
                      onClick={() => setForecastMonths(months)}
                      className="h-7 px-2.5 text-xs"
                    >
                      {t('budgets.forecastMonthsOption', { count: months })}
                    </Button>
                  ))}
                </div>
              </div>

              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {t('budgets.rolloverSummaryTitle')}
                </p>
                {rolloverSummaryError ? (
                  <div role="alert" className="flex items-center justify-between gap-3 py-3 text-sm text-destructive">
                    <span>{t('budgets.rolloverForecastError')}</span>
                    <Button type="button" variant="outline" size="sm" onClick={() => { void refetchRolloverSummary() }}>
                      {t('common.retry')}
                    </Button>
                  </div>
                ) : rolloverSummaryPending || !rolloverSummary ? (
                  <div className="space-y-2">
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                  </div>
                ) : rolloverSummary.categories.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-4">{t('budgets.rolloverForecastEmpty')}</p>
                ) : (
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full text-xs">
                      <thead className="bg-muted/40 text-muted-foreground">
                        <tr>
                          <th className="px-3 py-2 text-start font-medium">{t('budgets.category')}</th>
                          <th className="px-3 py-2 text-end font-medium">{t('budgets.colBaseBudget')}</th>
                          <th className="px-3 py-2 text-end font-medium">{t('budgets.colCarryover')}</th>
                          <th className="px-3 py-2 text-end font-medium">{t('budgets.colAvailable')}</th>
                          <th className="px-3 py-2 text-end font-medium">{t('budgets.colProjectedSpend')}</th>
                          <th className="px-3 py-2 text-end font-medium">{t('budgets.colRemaining')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {rolloverSummary.categories.map((cat) => (
                          <tr key={cat.category_id}>
                            <td className="px-3 py-2 text-start font-medium text-foreground">
                              <span>{cat.category_name}</span>
                              {cat.rollover_enabled && (
                                <span className="ms-2 inline-flex items-center rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                                  {t('budgets.rolloverEnabled')}
                                </span>
                              )}
                            </td>
                            <td className="px-3 py-2 text-end tabular-nums text-foreground">{fmt(cat.budgeted)}</td>
                            <td className="px-3 py-2 text-end tabular-nums text-muted-foreground">{fmtSigned(cat.carryover_in)}</td>
                            <td className="px-3 py-2 text-end tabular-nums font-medium text-foreground">{fmt(cat.effective_budget)}</td>
                            <td className="px-3 py-2 text-end tabular-nums text-foreground">{fmt(cat.projected_spent)}</td>
                            <td className={`px-3 py-2 text-end tabular-nums font-medium ${cat.remaining < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'}`}>
                              {fmtSigned(cat.remaining)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                      <tfoot className="border-t border-border bg-muted/20 font-semibold">
                        <tr>
                          <td className="px-3 py-2 text-start text-foreground">{t('budgets.netSurplus')}</td>
                          <td className="px-3 py-2 text-end tabular-nums text-foreground">{fmt(rolloverSummary.total_budgeted)}</td>
                          <td className="px-3 py-2 text-end tabular-nums text-muted-foreground">{fmtSigned(rolloverSummary.total_carryover)}</td>
                          <td className="px-3 py-2 text-end tabular-nums text-foreground">{fmt(rolloverSummary.total_effective_budget)}</td>
                          <td className="px-3 py-2 text-end tabular-nums text-foreground">{fmt(rolloverSummary.total_projected_spent)}</td>
                          <td className={`px-3 py-2 text-end tabular-nums ${rolloverSummary.net_surplus < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'}`}>
                            {fmtSigned(rolloverSummary.net_surplus)}
                          </td>
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                )}
              </div>

              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {t('budgets.forecastTableTitle')}
                </p>
                {forecastError ? (
                  <div role="alert" className="flex items-center justify-between gap-3 py-3 text-sm text-destructive">
                    <span>{t('budgets.rolloverForecastError')}</span>
                    <Button type="button" variant="outline" size="sm" onClick={() => { void refetchForecast() }}>
                      {t('common.retry')}
                    </Button>
                  </div>
                ) : forecastPending || !forecastData ? (
                  <div className="space-y-2">
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                  </div>
                ) : forecastData.items.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-4">{t('budgets.rolloverForecastEmpty')}</p>
                ) : (
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full text-xs">
                      <thead className="bg-muted/40 text-muted-foreground">
                        <tr>
                          <th className="px-3 py-2 text-start font-medium">{t('budgets.colMonth')}</th>
                          <th className="px-3 py-2 text-end font-medium">{t('budgets.colBaseBudget')}</th>
                          <th className="px-3 py-2 text-end font-medium">{t('budgets.colCarryover')}</th>
                          <th className="px-3 py-2 text-end font-medium">{t('budgets.colAvailable')}</th>
                          <th className="px-3 py-2 text-end font-medium">{t('budgets.colProjectedSpend')}</th>
                          <th className="px-3 py-2 text-end font-medium">{t('budgets.colVariance')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {forecastData.items.map((item) => {
                          const label = monthLabel(item.month.slice(0, 7), uiLocale).replace(/^\p{L}/u, (letter) => letter.toLocaleUpperCase(uiLocale))
                          return (
                            <tr key={item.month}>
                              <td className="px-3 py-2 text-start font-medium text-foreground">{label}</td>
                              <td className="px-3 py-2 text-end tabular-nums text-foreground">{fmt(item.projected_budget)}</td>
                              <td className="px-3 py-2 text-end tabular-nums text-muted-foreground">{fmtSigned(item.carryover_balance)}</td>
                              <td className="px-3 py-2 text-end tabular-nums font-medium text-foreground">{fmt(item.effective_budget)}</td>
                              <td className="px-3 py-2 text-end tabular-nums text-foreground">{fmt(item.projected_spend)}</td>
                              <td className={`px-3 py-2 text-end tabular-nums font-medium ${item.projected_variance < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'}`}>
                                {fmtSigned(item.projected_variance)}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}
        </SectionCard>
      </div>

      <Dialog open={suggestOpen} onOpenChange={(open) => { setSuggestOpen(open); if (!open) setSuggestEdits({}) }}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('budgets.suggestTitle')}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="flex items-center gap-4">
              <div className="space-y-1">
                <Label htmlFor={`${id}-suggest-period`}>{t('budgets.suggestPeriod')}</Label>
                <select
                  id={`${id}-suggest-period`}
                  value={suggestMonths}
                  onChange={(event) => { setSuggestMonths(Number(event.target.value)); setSuggestEdits({}) }}
                  className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  {[3, 6, 12].map((months) => <option key={months} value={months}>{months} {t('budgets.suggestMonths')}</option>)}
                </select>
              </div>
              <label htmlFor={`${id}-suggest-recurring`} className="flex items-center gap-2 cursor-pointer mt-5">
                <input id={`${id}-suggest-recurring`} type="checkbox" checked={suggestRecurring} onChange={(event) => setSuggestRecurring(event.target.checked)} className="rounded border-border" />
                <span className="text-sm text-foreground">{t('budgets.repeatEveryMonth')}</span>
              </label>
            </div>
            <p className="text-sm text-muted-foreground">{t('budgets.suggestDescription', { months: suggestMonths })}</p>
            {suggestionsPending ? (
              <p className="text-sm text-muted-foreground text-center py-8">{t('common.loading')}</p>
            ) : suggestionsError ? (
              <p role="alert" className="text-sm text-destructive text-center py-8">{t('budgets.suggestError')}</p>
            ) : suggestable.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">{t('budgets.suggestEmpty')}</p>
            ) : (
              <div className="max-h-72 overflow-y-auto rounded-lg border border-border">
                {suggestable.map((suggestion) => {
                  const row = rowFor(suggestion)
                  return (
                    <div key={suggestion.category_id} className="flex items-center gap-3 border-b border-border last:border-0 px-3 py-2">
                      <input
                        type="checkbox"
                        checked={row.checked}
                        onChange={(event) => setSuggestEdits((previous) => ({ ...previous, [suggestion.category_id]: { ...rowFor(suggestion), checked: event.target.checked } }))}
                        className="rounded border-border"
                        aria-label={suggestion.category_name}
                      />
                      <span className="flex-1 text-sm font-medium text-foreground truncate">{suggestion.category_name}</span>
                      <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap">{t('budgets.suggestSpent', { amount: fmt(Number(suggestion.total_spent)) })}</span>
                      <Input
                        type="number"
                        step="0.01"
                        min="0"
                        value={row.amount}
                        onChange={(event) => setSuggestEdits((previous) => ({ ...previous, [suggestion.category_id]: { ...rowFor(suggestion), amount: event.target.value } }))}
                        className="w-24 h-8"
                        aria-label={`${suggestion.category_name} ${t('budgets.amount')}`}
                      />
                    </div>
                  )
                })}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setSuggestOpen(false)}>{t('common.cancel')}</Button>
            <Button type="button" disabled={batchMutation.isPending || batchItems.length === 0} onClick={() => batchMutation.mutate()}>{t('budgets.suggestCreate')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <BudgetCopyDialog
        // A fresh mount per opening resets the source month and the strategy.
        key={copyOpen ? selectedMonth : 'closed'}
        open={copyOpen}
        onOpenChange={setCopyOpen}
        targetMonth={selectedMonth}
        targetBudgets={budgetsList ?? []}
        targetRefreshing={budgetsFetching}
      />

      {/* Render after the Save bar so its backdrop also covers those controls. */}
      {drillDownCategoryId !== null && (
        <TransactionDrillDown
          useViewerShare={activeAccountIds === null}
          filter={{
            title: t('dashboard.drillDownCategory', {
              category: allCategories.find((category) => category.id === drillDownCategoryId)?.name ?? drillDownCategoryId,
              month: monthLabel(selectedMonth, uiLocale),
            }),
            category_id: drillDownCategoryId,
            type: 'debit',
            ...monthRange(selectedMonth),
            account_ids: activeAccountIds ?? undefined,
          }}
          onClose={() => setDrillDownCategoryId(null)}
        />
      )}
    </div>
  )
}
