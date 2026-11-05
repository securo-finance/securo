import { useState, useEffect, useMemo, useRef } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { currentMonth, shiftMonth, monthLastDay, monthLabel, monthRange } from '@/lib/month-utils'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { format } from 'date-fns'
import { dashboard, transactions, budgets, categories as categoriesApi, categoryGroups as categoryGroupsApi, accounts as accountsApi, goals as goalsApi, groups as groupsApi, payees as payeesApi, rules as rulesApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { MonthPicker } from '@/components/ui/monthpicker'
import { Button } from '@/components/ui/button'
import { CalendarIcon, SlidersHorizontal } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { PageHeader } from '@/components/page-header'
import { PageRefreshButton } from '@/components/page-refresh-button'
import { TransactionDrillDown, type DrillDownFilter } from '@/components/transaction-drill-down'
import {
  BalanceEvolutionSection,
  GoalsProgressSection,
  PeriodTransactionsSection,
  SpendingChartsSection,
  UncategorizedBannerSection,
  HeroBalanceSection,
} from '@/components/dashboard/widget-components'
import { DashboardStack } from '@/components/dashboard/dashboard-stack'
import { CustomizeDashboardDialog } from '@/components/dashboard/customize-dashboard-dialog'
import { DashboardLayoutRecoveryDialog } from '@/components/dashboard/dashboard-layout-recovery-dialog'
import { useDashboardLayout } from '@/hooks/use-dashboard-layout'
import { TransactionDialog, type TransactionSavePayload } from '@/components/transaction-dialog'
import { extractApiError } from '@/lib/api-errors'
import type { TransactionsViewMode } from '@/components/transactions-view-switcher'
import { RuleDialog, type RuleDialogInitialData } from '@/components/rule-dialog'
import { useIsMobile } from '@/hooks/use-mobile'
import { useAuth } from '@/contexts/auth-context'
import { useCollectionFilter } from '@/contexts/collection-filter-context'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import type { ReactNode } from 'react'
import type { DashboardWidgetId } from '@/lib/dashboard-layout'
import type { DisplayRow } from '@/components/dashboard/widget-components'
import type { Rule, Transaction } from '@/types'
import { shouldShowPendingBadge } from '@/lib/transaction-status'

function parseHashtags(notes: string | null): string[] {
  if (!notes) return []
  const matches = notes.match(/#[\w\u00C0-\u017E-]+/g)
  return matches ?? []
}

const MONTH_REGEX = /^\d{4}-\d{2}$/
const MONTH_STRING_LENGTH = 7
const COMPARISON_MONTH_PARAM = 'comparison_month'
const EMPTY_COMPARISON_MONTHS: string[] = []

function normalizeComparisonMonths(months: string[], availableMonths: string[], viewedMonth: string): string[] {
  const available = new Set(availableMonths)
  return [...new Set(months)]
    .filter((month) => MONTH_REGEX.test(month) && month < viewedMonth && available.has(month))
    .sort()
    .slice(-24)
}

function defaultComparisonMonths(availableMonths: string[], viewedMonth: string): string[] {
  const firstMonth = shiftMonth(viewedMonth, -12)
  return availableMonths
    .filter((month) => MONTH_REGEX.test(month) && month >= firstMonth && month < viewedMonth)
    .sort()
}

function parseMonthFromParams(params: URLSearchParams): string | null {
  const monthParam = params.get('month')
  if (monthParam && MONTH_REGEX.test(monthParam)) {
    return monthParam
  }

  const fromParam = params.get('from')
  if (fromParam && fromParam.length >= MONTH_STRING_LENGTH) {
    const parsedMonth = fromParam.substring(0, MONTH_STRING_LENGTH)
    if (MONTH_REGEX.test(parsedMonth)) {
      return parsedMonth
    }
  }

  return null
}


export default function DashboardPage() {
  const { t, i18n } = useTranslation()
  const isMobile = useIsMobile()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const displayName = user?.preferences?.display_name || ''

  const greeting = (() => {
    const hour = new Date().getHours()
    const key = hour < 12 ? 'greetingMorning' : hour < 18 ? 'greetingAfternoon' : 'greetingEvening'
    const base = t(`dashboard.${key}`)
    return displayName ? `${base}, ${displayName}` : base
  })()
  const [searchParams, setSearchParams] = useSearchParams()
  const [selectedMonth, setSelectedMonth] = useState<string>(() => {
    return parseMonthFromParams(searchParams) ?? currentMonth()
  })
  const [drillDown, setDrillDown] = useState<DrillDownFilter | null>(null)
  // Transactions section view, mirroring the transactions page: the choice
  // lives in the URL so the section can be refreshed, bookmarked or shared.
  const [txViewMode, setTxViewMode] = useState<TransactionsViewMode>(() => (
    searchParams.get('view') === 'calendar' ? 'calendar' : 'list'
  ))
  const [calendarSelectedDate, setCalendarSelectedDate] = useState<string>(() => searchParams.get('day') ?? '')

  const prevSearchRef = useRef<string | null>(null)
  const searchParamsRef = useRef(searchParams)
  searchParamsRef.current = searchParams

  // Sync state from URL when navigating (e.g. back/forward button)
  useEffect(() => {
    const search = searchParams.toString()
    if (prevSearchRef.current === search) return
    const isInitial = prevSearchRef.current === null
    prevSearchRef.current = search

    const parsedMonth = parseMonthFromParams(searchParams)
    if (parsedMonth) {
      setSelectedMonth(parsedMonth)
    } else if (!isInitial) {
      setSelectedMonth(currentMonth())
    }
    setTxViewMode(searchParams.get('view') === 'calendar' ? 'calendar' : 'list')
    setCalendarSelectedDate(searchParams.get('day') ?? '')
  }, [searchParams])

  // Sync selectedMonth and the transactions view back to URL
  useEffect(() => {
    const params = new URLSearchParams(searchParamsRef.current.toString())
    if (selectedMonth) {
      params.set('month', selectedMonth)
      params.delete('from')
      params.delete('to')
    } else {
      params.delete('month')
    }

    if (txViewMode === 'calendar') {
      params.set('view', 'calendar')
      if (calendarSelectedDate) params.set('day', calendarSelectedDate)
      else params.delete('day')
    } else {
      params.delete('view')
      params.delete('day')
    }

    setSearchParams(params, { replace: true })
  }, [selectedMonth, txViewMode, calendarSelectedDate, setSearchParams])
  const [editingTx, setEditingTx] = useState<Transaction | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [createRuleOpen, setCreateRuleOpen] = useState(false)
  const [createRuleInitialData, setCreateRuleInitialData] = useState<RuleDialogInitialData | undefined>(undefined)
  const queryClient = useQueryClient()
  const [headerCalOpen, setHeaderCalOpen] = useState(false)
  const [hoveredDay, setHoveredDay] = useState<number | null>(null)
  const dateFnsLocale = resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language)
  const { from: monthStart, to: monthEnd } = monthRange(selectedMonth)
  const monthParam = monthStart
  const uiLocale = i18n.resolvedLanguage ?? i18n.language
  const monthLabelStr = monthLabel(selectedMonth, uiLocale)

  const handleMonthChange = (newMonth: string) => {
    setSelectedMonth(newMonth)
}

  // Active Collection filter (issue #105): scope dashboard cards to its
  // accounts. undefined when "All accounts".
  const { activeAccountIds, activeWalletIds } = useCollectionFilter()
  const acctIds = activeAccountIds ?? undefined
  const walletIds = activeWalletIds ?? undefined
  // A wallet-only collection (active, but with zero accounts) has no account
  // data — skip the account-only cards so they render empty instead of
  // silently falling back to "all accounts".
  const noAccounts = activeAccountIds !== null && activeAccountIds.length === 0

  const { data: comparisonMonthOptions, isLoading: comparisonMonthsLoading } = useQuery({
    queryKey: ['dashboard', 'balance-history-months', activeAccountIds],
    queryFn: () => dashboard.balanceHistoryMonths(acctIds),
    enabled: !noAccounts,
    staleTime: 60_000,
  })
  const availableComparisonMonths = comparisonMonthOptions ?? EMPTY_COMPARISON_MONTHS
  const defaultMonths = useMemo(
    () => defaultComparisonMonths(availableComparisonMonths, selectedMonth),
    [availableComparisonMonths, selectedMonth],
  )
  const requestedComparisonMonths = useMemo(
    () => searchParams.getAll(COMPARISON_MONTH_PARAM),
    [searchParams],
  )
  const validComparisonMonths = useMemo(
    () => normalizeComparisonMonths(requestedComparisonMonths, availableComparisonMonths, selectedMonth),
    [requestedComparisonMonths, availableComparisonMonths, selectedMonth],
  )
  const comparisonMonths = validComparisonMonths.length > 0 ? validComparisonMonths : defaultMonths

  useEffect(() => {
    if (comparisonMonthsLoading || !comparisonMonthOptions || requestedComparisonMonths.length === 0) return
    const urlMonth = parseMonthFromParams(searchParams)
    if (urlMonth && urlMonth !== selectedMonth) return
    if (
      requestedComparisonMonths.length === validComparisonMonths.length &&
      requestedComparisonMonths.every((month, index) => month === validComparisonMonths[index])
    ) return
    const params = new URLSearchParams(searchParams.toString())
    params.delete(COMPARISON_MONTH_PARAM)
    validComparisonMonths.forEach((month) => params.append(COMPARISON_MONTH_PARAM, month))
    setSearchParams(params, { replace: true })
  }, [comparisonMonthsLoading, comparisonMonthOptions, requestedComparisonMonths,
    validComparisonMonths, selectedMonth, searchParams, setSearchParams])

  const toggleComparisonMonth = (month: string, checked: boolean) => {
    const nextMonths = checked
      ? [...comparisonMonths, month]
      : comparisonMonths.filter((selected) => selected !== month)
    if (nextMonths.length === 0 || nextMonths.length > 24) return
    const params = new URLSearchParams(searchParams.toString())
    params.delete(COMPARISON_MONTH_PARAM)
    const orderedMonths = [...new Set(nextMonths)].sort()
    orderedMonths.forEach((selected) => params.append(COMPARISON_MONTH_PARAM, selected))
    setSearchParams(params)
  }

  const { data: summary, isLoading: summaryLoading, isFetching: summaryFetching } = useQuery({
    queryKey: ['dashboard', 'summary', selectedMonth, activeAccountIds, activeWalletIds],
    queryFn: () => dashboard.summary(monthParam, undefined, acctIds, walletIds),
  })

  const { data: spending, isLoading: spendingLoading } = useQuery({
    queryKey: ['dashboard', 'spending', selectedMonth, activeAccountIds],
    queryFn: () => dashboard.spendingByCategory(monthParam, acctIds),
    enabled: !noAccounts,
  })

  const { data: balanceHistory, isLoading: balanceHistoryLoading } = useQuery({
    queryKey: ['dashboard', 'balance-history', selectedMonth, activeAccountIds, comparisonMonths],
    queryFn: () => dashboard.balanceHistory(
      monthParam,
      acctIds,
      comparisonMonths.length > 0 ? comparisonMonths.map((value) => `${value}-01`) : undefined,
    ),
    enabled: !noAccounts && !comparisonMonthsLoading,
  })

  const { data: currentMonthTxs, isLoading: currentTxLoading } = useQuery({
    queryKey: ['transactions', 'cumulative', selectedMonth, activeAccountIds],
    queryFn: () => transactions.list({
      from: monthStart,
      to: monthEnd,
      limit: 500,
      exclude_transfers: true,
      account_ids: acctIds,
    }),
    enabled: !noAccounts,
  })

  // Same month grid the transactions page renders, scoped to the active
  // collection's accounts. Only fetched while the calendar is on screen.
  const calendarAccountIds = acctIds && acctIds.length > 0 ? acctIds : undefined
  const { data: calendarData, isLoading: calendarLoading } = useQuery({
    queryKey: ['transactions', 'calendar', selectedMonth, activeAccountIds],
    enabled: txViewMode === 'calendar' && !noAccounts,
    queryFn: () => transactions.calendar({
      month: monthStart,
      account_ids: calendarAccountIds,
    }),
  })

  // Resolve group_id → name for the badge on split transactions.
  const { data: allGroups } = useQuery({
    queryKey: ['groups', 'all'],
    queryFn: () => groupsApi.list(true),
    staleTime: 60_000,
  })
  const groupNameById = useMemo(() => {
    const map = new Map<string, string>()
    for (const g of allGroups ?? []) map.set(g.id, g.name)
    return map
  }, [allGroups])

  const { data: projectedTxs, isLoading: projectedTxLoading } = useQuery({
    queryKey: ['dashboard', 'projected-transactions', selectedMonth],
    queryFn: () => dashboard.projectedTransactions({ month: monthParam }),
  })

  const { data: budgetComparison } = useQuery({
    queryKey: ['budgets', 'comparison', selectedMonth],
    queryFn: () => budgets.comparison(monthParam),
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const { data: categoryGroupsList } = useQuery({
    queryKey: ['categoryGroups'],
    queryFn: categoryGroupsApi.list,
  })

  const { data: accountsList, isLoading: accountsLoading, isError: accountsError } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const { data: payeesList } = useQuery({
    queryKey: ['payees'],
    queryFn: payeesApi.list,
  })

  const { data: goalsSummary } = useQuery({
    queryKey: ['goals', 'summary'],
    queryFn: () => goalsApi.summary(3),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: TransactionSavePayload & { id: string }) =>
      transactions.update(id, data),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setDialogOpen(false)
      setEditingTx(null)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => transactions.delete(id),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setDialogOpen(false)
      setEditingTx(null)
    },
  })

  const unlinkTransferMutation = useMutation({
    mutationFn: (pairId: string) => transactions.unlinkTransfer(pairId),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setDialogOpen(false)
      setEditingTx(null)
    },
  })

  const createRuleMutation = useMutation({
    mutationFn: (data: Omit<Rule, 'id' | 'user_id'>) => rulesApi.create(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      setCreateRuleOpen(false)
      setCreateRuleInitialData(undefined)
      const applied = result.applied_count ?? 0
      if (applied > 0) {
        invalidateFinancialQueries(queryClient)
        queryClient.invalidateQueries({ queryKey: ['payees'] })
        toast.success(t('rules.createdAndApplied', { count: applied }))
      } else {
        toast.success(t('rules.created'))
      }
    },
    onError: (error: unknown) => {
      const err = error as { response?: { status?: number } }
      if (err?.response?.status === 409) {
        toast.error(t('rules.duplicateName'))
      } else {
        toast.error(t('common.error'))
      }
    },
  })

  const handleCreateRuleFromTransaction = (tx: Transaction) => {
    const conditions = [
      { field: 'description', op: 'contains', value: tx.description },
    ]
    if (tx.payee_id) {
      conditions.push({ field: 'payee_id', op: 'equals', value: tx.payee_id })
    }
    const actions = tx.category_id
      ? [{ op: 'set_category', value: tx.category_id }]
      : [{ op: 'set_category', value: '' }]
    const tags = parseHashtags(tx.notes)
    if (tags.length > 0) {
      actions.push({ op: 'append_notes', value: tags.join(' ') })
    }
    setCreateRuleInitialData({ conditions, actions })
    setCreateRuleOpen(true)
  }

  // Calendar rows carry only an id, so the full transaction is fetched before
  // the edit dialog opens (same as the transactions page).
  const handleOpenCalendarTransaction = async (id: string) => {
    try {
      const tx = await transactions.get(id)
      setEditingTx(tx)
      setDialogOpen(true)
    } catch {
      toast.error(t('common.error'))
    }
  }


  const cumulativeData = useMemo(() => {
    if (!balanceHistory) return []
    const daysInMonth = monthLastDay(selectedMonth)
    const opening = balanceHistory.opening_balance
    const result: {
      day: number
      current: number | null
      comparison: number | null
      median: number | null
      band: [number, number] | null
    }[] = []
    let lastComparison: number | null = null
    for (let day = 1; day <= daysInMonth; day++) {
      const cur = balanceHistory.current.find(d => d.day === day)
      const comparisonPoint = balanceHistory.comparison?.find(d => d.day === day)
      if (comparisonPoint?.balance != null && balanceHistory.comparison_opening_balance != null) {
        lastComparison = comparisonPoint.balance - balanceHistory.comparison_opening_balance
      } else if (comparisonPoint) {
        lastComparison = null
      }
      const base = balanceHistory.baseline.find(d => d.day === day)
      const hasBand = base?.lower_quartile != null && base?.upper_quartile != null
      result.push({
        day,
        current: cur?.balance != null ? cur.balance - opening : null,
        comparison: lastComparison,
        median: base?.median ?? null,
        band: hasBand ? [base!.lower_quartile!, base!.upper_quartile!] : null,
      })
    }
    return result
  }, [balanceHistory, selectedMonth])

  const lastCurrentPoint = [...cumulativeData].reverse().find(d => d.current !== null)
  const lastDay = lastCurrentPoint?.day ?? 0
  const monthVariation = lastCurrentPoint?.current ?? 0

  const primaryCurrency = summary?.primary_currency ?? userCurrency
  const totalBalance = summary?.total_balance_primary ?? Object.values(summary?.total_balance ?? {}).reduce((a, b) => a + Number(b), 0)
  const projectedBalance = summary?.projected_balance_primary ?? Object.values(summary?.projected_balance ?? {}).reduce((a, b) => a + Number(b), 0)
  const hasProjectedBalance = Math.abs(projectedBalance - totalBalance) >= 0.01
  const assetsValue = summary?.assets_value_primary ?? Object.values(summary?.assets_value ?? {}).reduce((a, b) => a + b, 0)

  // Available balance: checking/savings accounts only — what's actually
  // spendable today, as opposed to `totalBalance` (net worth: accounts +
  // investments - open card bills). Scoped to the active Collection filter,
  // same as the rest of the dashboard.
  const availableBalanceAccounts = useMemo(() => {
    const all = accountsList ?? []
    const scoped = activeAccountIds ? all.filter((a) => activeAccountIds.includes(a.id)) : all
    return scoped.filter((a) => a.type === 'checking' || a.type === 'savings')
  }, [accountsList, activeAccountIds])
  const availableBalance = availableBalanceAccounts.reduce(
    (sum, a) => sum + Number(a.balance_primary ?? a.current_balance), 0,
  )
  // While accounts are loading or failed to load, treat their balance
  // components as unavailable rather than silently rendering zero.
  const accountsUnavailable = accountsLoading || accountsError
  const creditCardAccounts = (accountsList ?? [])
    .filter((a) => (activeAccountIds ? activeAccountIds.includes(a.id) : true) && a.type === 'credit_card')
  // Net worth separates loans from spendable non-card balances.
  const loanBalance = (accountsList ?? [])
    .filter((a) => (activeAccountIds ? activeAccountIds.includes(a.id) : true) && a.type === 'loan')
    .reduce((sum, a) => sum + Number(a.balance_primary ?? a.current_balance), 0)
  const nonCardAccountsBalance = (accountsList ?? [])
    .filter((a) => (activeAccountIds ? activeAccountIds.includes(a.id) : true) && a.type !== 'credit_card' && a.type !== 'loan')
    .reduce((sum, a) => sum + Number(a.balance_primary ?? a.current_balance), 0)

  // Savings rate & projection
  const income = Number(summary?.monthly_income_primary ?? summary?.monthly_income ?? 0)
  const expenses = Number(summary?.monthly_expenses_primary ?? summary?.monthly_expenses ?? 0)
  // What the month is still expected to close at, once recurring entries that
  // have not posted yet are counted. Rendered only when it differs from the
  // realised figure, so a month with nothing pending stays quiet.
  const projectedIncome = Number(summary?.projected_income_primary ?? summary?.projected_income ?? income)
  const projectedExpenses = Number(summary?.projected_expenses_primary ?? summary?.projected_expenses ?? expenses)
  const savingsRate = income > 0 ? ((income - expenses) / income) * 100 : 0
  const isCurrentMonth = selectedMonth === currentMonth()
  const daysElapsed = isCurrentMonth ? new Date().getDate() : monthLastDay(selectedMonth)
  const daysInMonth = monthLastDay(selectedMonth)
  const projectedSpend = expenses > 0 && isCurrentMonth && daysElapsed > 0
    ? (expenses / daysElapsed) * daysInMonth
    : null

  // Uncategorized data
  const uncategorizedCount = summary?.pending_categorization ?? 0
  const uncategorizedAmount = summary?.pending_categorization_amount ?? 0

  const [catSortDesc, setCatSortDesc] = useState(true)

  // Merged category bars data
  const mergedCategories = useMemo(() => {
    if (!spending) return []
    const budgetMap = new Map<string, (typeof budgetComparison extends (infer T)[] | undefined ? T : never)>()
    if (budgetComparison) {
      for (const b of budgetComparison) {
        budgetMap.set(b.category_id, b)
      }
    }
    return spending
      .filter(s => s.category_id !== null)
      .map(s => {
        const budget = s.category_id ? budgetMap.get(s.category_id) : undefined
        // The category widget must show the same spend set as its drill-down:
        // settled transactions plus pending/future rows and recurring
        // projections. The API keeps `total` as settled-only for callers that
        // need the actual/forecast split, while `projected_total` is the
        // user-visible all-in amount.
        const actual = s.projected_total
        const prevAmount = budget ? Number(budget.projected_prev_month_amount) : 0
        let momPct: number | null = null
        if (prevAmount > 0) {
          momPct = ((actual - prevAmount) / prevAmount) * 100
        } else if (actual > 0) {
          momPct = 100
        }
        return {
          category_id: s.category_id!,
          category_name: s.category_name,
          category_icon: s.category_icon,
          category_color: s.category_color,
          actual,
          budget_amount: budget ? Number(budget.budget_amount) : null,
          percentage_used: budget?.percentage_used ?? null,
          momPct,
        }
      })
      .sort((a, b) => catSortDesc ? b.actual - a.actual : a.actual - b.actual)
  }, [spending, budgetComparison, catSortDesc])

  const [txPage, setTxPage] = useState(1)
  const [txSortDesc, setTxSortDesc] = useState(true)
  useEffect(() => setTxPage(1), [selectedMonth])

  const [txPerPage, setTxPerPage] = useState<number>(() => {
    try {
      const stored = localStorage.getItem('securo.dashboard.pageSize')
      return stored ? Number(stored) : 10
    } catch {
      return 10
    }
  })
  const allDisplayRows = useMemo(() => {
    const rows: DisplayRow[] = []
    for (const tx of currentMonthTxs?.items ?? []) {
      const isShared = !!tx.is_shared
      const displayAmount =
        isShared && tx.viewer_share != null ? Number(tx.viewer_share) : Number(tx.amount)
      const groupId = tx.group_id ?? null
      // Owner-side share: backend populates viewer_share for owners
      // who participate in their own split. Suppress when it equals
      // the parent amount (sole-member case = no useful info).
      const ownerShareRaw =
        !isShared && tx.viewer_share != null ? Number(tx.viewer_share) : null
      const ownerShare =
        ownerShareRaw != null && Math.abs(ownerShareRaw) !== Math.abs(Number(tx.amount))
          ? ownerShareRaw
          : null
      rows.push({
        key: tx.id,
        description: tx.description,
        date: tx.date,
        type: tx.type,
        amount: displayAmount,
        amountPrimary: tx.amount_primary != null ? Number(tx.amount_primary) : null,
        currency: tx.currency,
        categoryIcon: tx.category?.icon ?? null,
        categoryName: tx.category?.name ?? null,
        categoryColor: tx.category?.color ?? null,
        accountId: tx.account_id ?? null,
        isProjected: false,
        attachmentCount: tx.attachment_count ?? 0,
        isShared,
        parentTotal: isShared ? Number(tx.amount) : null,
        ownerShare,
        groupId,
        parentOwnerName: isShared ? tx.parent_owner_name ?? null : null,
        groupName: groupId ? groupNameById.get(groupId) ?? null : null,
        isIgnored: tx.is_ignored,
        installmentNumber: tx.installment_number,
        totalInstallments: tx.total_installments,
        showPendingBadge: shouldShowPendingBadge(tx),
      })
    }
    for (const pt of projectedTxs ?? []) {
      rows.push({
        key: `proj-${pt.recurring_id}-${pt.date}`,
        description: pt.description,
        date: pt.date,
        type: pt.type,
        amount: pt.amount,
        amountPrimary: pt.amount_primary ?? null,
        currency: pt.currency,
        categoryIcon: pt.category_icon,
        categoryName: pt.category_name,
        categoryColor: pt.category_color ?? null,
        accountId: pt.account_id,
        isProjected: true,
        attachmentCount: 0,
        isShared: false,
        parentTotal: null,
        ownerShare: null,
        groupId: null,
        parentOwnerName: null,
        groupName: null,
        isIgnored: false,
        installmentNumber: null,
        totalInstallments: null,
        showPendingBadge: false,
      })
    }
    rows.sort((a, b) => txSortDesc ? b.date.localeCompare(a.date) : a.date.localeCompare(b.date))
    return rows
  }, [currentMonthTxs, projectedTxs, txSortDesc, groupNameById])

  const txTotalPages = Math.ceil(allDisplayRows.length / txPerPage)
  const pagedRows = allDisplayRows.slice((txPage - 1) * txPerPage, txPage * txPerPage)
  const txListLoading = currentTxLoading || projectedTxLoading

  // Savings rate display
  const savingsRateColor = income === 0 && expenses > 0
    ? 'text-rose-500'
    : savingsRate > 0
      ? 'text-emerald-600'
      : savingsRate < 0
        ? 'text-rose-500'
        : 'text-muted-foreground'

  const savingsRateDisplay = income === 0 && expenses > 0
    ? '---'
    : `${savingsRate.toFixed(0)}%`

  const layout = useDashboardLayout()
  const customizeTriggerRef = useRef<HTMLButtonElement>(null)
  const reviewTriggerRef = useRef<HTMLButtonElement>(null)
  const refreshTriggerRef = useRef<HTMLButtonElement>(null)
  const [recoveryFocusGuard, setRecoveryFocusGuard] = useState<() => boolean>(() => () => false)

  const sections: Record<DashboardWidgetId, ReactNode> = {
    'hero-balance': (
      <HeroBalanceSection
        summary={summary}
        summaryLoading={summaryLoading}
        accountsUnavailable={accountsUnavailable}
        availableBalance={availableBalance}
        availableBalanceAccounts={availableBalanceAccounts}
        primaryCurrency={primaryCurrency}
        totalBalance={totalBalance}
        projectedBalance={projectedBalance}
        hasProjectedBalance={hasProjectedBalance}
        assetsValue={assetsValue}
        creditCardAccounts={creditCardAccounts}
        nonCardAccountsBalance={nonCardAccountsBalance}
        loanBalance={loanBalance}
        income={income}
        expenses={expenses}
        projectedIncome={projectedIncome}
        projectedExpenses={projectedExpenses}
        savingsRateColor={savingsRateColor}
        savingsRateDisplay={savingsRateDisplay}
        projectedSpend={projectedSpend}
        monthStart={monthStart}
        monthEnd={monthEnd}
        monthLabelStr={monthLabelStr}
        userCurrency={userCurrency}
        setDrillDown={setDrillDown}
      />
    ),
    'uncategorized-banner': (
      <UncategorizedBannerSection
        summaryLoading={summaryLoading}
        uncategorizedCount={uncategorizedCount}
        uncategorizedAmount={uncategorizedAmount}
        userCurrency={userCurrency}
        setDrillDown={setDrillDown}
      />
    ),
    'spending-charts': (
      <SpendingChartsSection
        spendingLoading={spendingLoading}
        mergedCategories={mergedCategories}
        catSortDesc={catSortDesc}
        setCatSortDesc={setCatSortDesc}
        userCurrency={userCurrency}
        monthStart={monthStart}
        monthEnd={monthEnd}
        monthLabelStr={monthLabelStr}
        setDrillDown={setDrillDown}
      />
    ),
    'balance-evolution': (
      <BalanceEvolutionSection
        balanceHistoryLoading={balanceHistoryLoading}
        cumulativeData={cumulativeData}
        lastCurrentPoint={lastCurrentPoint}
        lastDay={lastDay}
        monthVariation={monthVariation}
        availableComparisonMonths={availableComparisonMonths}
        comparisonMonths={comparisonMonths}
        toggleComparisonMonth={toggleComparisonMonth}
        selectedMonth={selectedMonth}
        uiLocale={uiLocale}
        hoveredDay={hoveredDay}
        setHoveredDay={setHoveredDay}
        userCurrency={userCurrency}
        setDrillDown={setDrillDown}
      />
    ),
    'goals-progress': <GoalsProgressSection goalsSummary={goalsSummary} />,
    'period-transactions': (
      <PeriodTransactionsSection
        txViewMode={txViewMode}
        setTxViewMode={setTxViewMode}
        txSortDesc={txSortDesc}
        setTxSortDesc={setTxSortDesc}
        txPage={txPage}
        setTxPage={setTxPage}
        txPerPage={txPerPage}
        setTxPerPage={setTxPerPage}
        calendarData={calendarData}
        calendarLoading={calendarLoading}
        calendarSelectedDate={calendarSelectedDate}
        setCalendarSelectedDate={setCalendarSelectedDate}
        handleOpenCalendarTransaction={handleOpenCalendarTransaction}
        accountsList={accountsList}
        userCurrency={userCurrency}
        pagedRows={pagedRows}
        allDisplayRows={allDisplayRows}
        txTotalPages={txTotalPages}
        txListLoading={txListLoading}
        isMobile={isMobile}
        currentMonthTxs={currentMonthTxs}
        setEditingTx={setEditingTx}
        setDialogOpen={setDialogOpen}
      />
    ),
  }

  return (
    <div>
      {/* Header */}
      <PageHeader
        section={greeting}
        title={monthLabel(selectedMonth, uiLocale).replace(/^\w/, c => c.toUpperCase())}
        action={
          <div className="flex items-center gap-1">
            <PageRefreshButton onRefresh={() => queryClient.refetchQueries({ type: 'active' })} refreshing={summaryFetching} />
            {layout.draft.workspaceId && (
              <Button
                ref={customizeTriggerRef}
                variant="outline"
                size="sm"
                aria-haspopup="dialog"
                disabled={!layout.canCustomize}
                onClick={layout.openCustomize}
              >
                <SlidersHorizontal className="mr-1.5 h-4 w-4" aria-hidden="true" />
                {t('dashboard.customize.action')}
              </Button>
            )}
            <button
              className="h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:border-border hover:text-foreground transition-all text-base"
              onClick={() => handleMonthChange(shiftMonth(selectedMonth, -1))}
            >&#8249;</button>
            <Popover open={headerCalOpen} onOpenChange={setHeaderCalOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className="inline-flex items-center justify-center gap-2 border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground hover:bg-muted/50 transition-all cursor-pointer min-w-[180px]"
                >
                  <CalendarIcon className="size-3.5 text-muted-foreground" />
                  {monthLabel(selectedMonth, uiLocale).replace(/^\w/, c => c.toUpperCase())}
                </button>
              </PopoverTrigger>
              <PopoverContent align="center" className="w-auto p-0">
                <MonthPicker
                  locale={dateFnsLocale}
                  selectedMonth={new Date(`${selectedMonth}-01T00:00:00`)}
                  onMonthSelect={(date) => {
                    if (!date) return
                    const newMonth = format(date, 'yyyy-MM')
                    setSelectedMonth(newMonth)
                    setHeaderCalOpen(false)
                  }}
                />
              </PopoverContent>
            </Popover>
            <button
              className="h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:border-border hover:text-foreground transition-all text-base"
              onClick={() => handleMonthChange(shiftMonth(selectedMonth, 1))}
            >&#8250;</button>
          </div>
        }
      />

      {layout.persistence.pending && (
        <div role="status" aria-live="polite" aria-busy="true"
          className="mb-4 rounded-lg border border-border p-3">
          <p className="text-sm text-muted-foreground">{t('dashboard.customize.persistence.pending')}</p>
        </div>
      )}

      {layout.recovery && (
        <div role="status" aria-live="polite" aria-busy={layout.persistence.pending || layout.persistence.readPending}
          className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-3">
          <div className="space-y-1 text-sm">
            <p>{t('dashboard.customize.persistence.unsaved')}</p>
            <p className="text-muted-foreground">{t('dashboard.customize.persistence.retention')}</p>
          </div>
          <Button ref={reviewTriggerRef} variant="outline" size="sm" aria-haspopup="dialog" disabled={!layout.canReview}
            onClick={() => {
              if (!layout.recovery) return
              const guard = layout.captureRecoveryFocusGuard()
              setRecoveryFocusGuard(() => guard)
              layout.reviewRecovery(layout.recovery.attemptId)
            }}>
            {t('dashboard.customize.persistence.review')}
          </Button>
        </div>
      )}

      {layout.showRefreshDisplay && (
        <div role="status" aria-live="polite" aria-busy={layout.displayReadPending}
          className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-3">
          <p className="text-sm text-muted-foreground">{t('dashboard.customize.persistence.stale')}</p>
          <Button ref={refreshTriggerRef} variant="outline" size="sm" disabled={!layout.canRefreshDisplay}
            onClick={() => { void layout.refreshDisplay().catch(() => {}) }}>
            {t('dashboard.customize.persistence.refresh')}
          </Button>
        </div>
      )}

      {/* Composition loop (design packet §6.4): the resolved draft drives
          render order and visibility — while the customize panel is open
          the stack renders the live draft (immediate preview). */}
      <DashboardStack ordered={layout.orderedVisible} sections={sections} />

      <DashboardLayoutRecoveryDialog
        review={layout.recoveryReview}
        busy={layout.persistence.pending || layout.persistence.readPending}
        onClose={layout.closeRecoveryReview}
        onRecheck={layout.recheckReviewedRecovery}
        onReplace={layout.replaceRecovery}
        onDiscard={layout.discardRecovery}
        returnFocus={customizeTriggerRef}
        fallbackFocus={reviewTriggerRef}
        refreshFocus={refreshTriggerRef}
        canReturnFocus={recoveryFocusGuard}
      />

      <CustomizeDashboardDialog
        open={layout.customizeOpen}
        onOpenChange={(next) =>
          next ? layout.openCustomize() : layout.closeCustomize(true)
        }
        draft={layout.draft}
        canUndo={layout.canUndo}
        disabled={layout.customizeDisabled}
        busy={layout.persistence.pending || layout.persistence.readPending}
        canEdit={layout.canEditCustomize}
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          const trigger = customizeTriggerRef.current
          if (trigger?.isConnected && !trigger.disabled) trigger.focus()
        }}
        onReorder={layout.reorder}
        onMove={layout.move}
        onToggle={layout.toggleVisible}
        onReset={layout.reset}
        onUndo={layout.undoReset}
      />

      <TransactionDrillDown
        filter={
          drillDown
            ? {
                ...drillDown,
                // Keep drill-downs consistent with the collection-scoped cards
                // they open from (e.g. "Categorize now").
                account_ids:
                  drillDown.account_ids ?? (acctIds && acctIds.length > 0 ? acctIds : undefined),
              }
            : null
        }
        onClose={() => setDrillDown(null)}
        onTransactionClick={(tx) => { setEditingTx(tx); setDialogOpen(true) }}
      />

      <TransactionDialog
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setEditingTx(null) }}
        transaction={editingTx}
        categories={categoriesList ?? []}
        categoryGroups={categoryGroupsList ?? []}
        accounts={(accountsList ?? []).map((a) => ({
          id: a.id,
          name: getAccountName(a),
          display_name: a.display_name,
          type: a.type,
          currency: a.currency,
          secured_asset_id: a.secured_asset_id,
        }))}
        onSave={(data) => {
          if (editingTx) updateMutation.mutate({ id: editingTx.id, ...data })
        }}
        onDelete={() => {
          if (editingTx) deleteMutation.mutate(editingTx.id)
        }}
        onUnlinkTransfer={(pairId) => unlinkTransferMutation.mutate(pairId)}
        onCreateRule={(tx) => {
          setDialogOpen(false)
          setEditingTx(null)
          handleCreateRuleFromTransaction(tx)
        }}
        loading={updateMutation.isPending || deleteMutation.isPending || unlinkTransferMutation.isPending}
        error={updateMutation.error ? extractApiError(updateMutation.error) : deleteMutation.error ? extractApiError(deleteMutation.error) : null}
        isSynced={editingTx?.source === 'sync'}
      />

      <RuleDialog
        key={createRuleOpen ? 'rule-open' : 'rule-closed'}
        open={createRuleOpen}
        onClose={() => { setCreateRuleOpen(false); setCreateRuleInitialData(undefined) }}
        rule={null}
        categories={categoriesList ?? []}
        categoryGroups={categoryGroupsList ?? []}
        accounts={(accountsList ?? []).map((a: { id: string; name: string; display_name?: string | null }) => ({ id: a.id, name: getAccountName(a) }))}
        payees={payeesList ?? []}
        onSave={(data) => createRuleMutation.mutate(data as Omit<Rule, 'id' | 'user_id'>)}
        loading={createRuleMutation.isPending}
        initialData={createRuleInitialData}
      />
    </div>
  )
}
