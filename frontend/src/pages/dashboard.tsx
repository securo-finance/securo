import { useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { format } from 'date-fns'
import { ptBR, enUS } from 'date-fns/locale'
import { toast } from 'sonner'
import { dashboard, transactions, budgets, categories as categoriesApi, accounts as accountsApi, months } from '@/lib/api'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { Calendar } from '@/components/ui/calendar'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { CheckCircle2, CalendarIcon, Paperclip } from 'lucide-react'
import { PageHeader } from '@/components/page-header'
import { CategoryIcon } from '@/components/category-icon'
import { TransactionDrillDown, type DrillDownFilter } from '@/components/transaction-drill-down'
import { TransactionDialog, extractApiError } from '@/components/transaction-dialog'
import { useCurrentMonth } from '@/hooks/use-current-month'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import type { Transaction } from '@/types'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

function currentMonth() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function shiftMonth(yearMonth: string, delta: number) {
  const [y, m] = yearMonth.split('-').map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function monthLastDay(yearMonth: string) {
  const [y, m] = yearMonth.split('-').map(Number)
  return new Date(y, m, 0).getDate()
}

function monthLabel(yearMonth: string, locale = 'pt-BR') {
  const [y, m] = yearMonth.split('-').map(Number)
  return new Date(y, m - 1, 2).toLocaleDateString(locale, { month: 'long', year: 'numeric' })
}

function formatDate(dateStr: string, locale = 'pt-BR') {
  return new Date(dateStr + 'T00:00:00').toLocaleDateString(locale)
}


export default function DashboardPage() {
  const { t, i18n } = useTranslation()
  const { mask, privacyMode, MASK } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const displayName = user?.preferences?.display_name || ''
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language

  const greeting = (() => {
    const hour = new Date().getHours()
    const key = hour < 12 ? 'greetingMorning' : hour < 18 ? 'greetingAfternoon' : 'greetingEvening'
    const base = t(`dashboard.${key}`)
    return displayName ? `${base}, ${displayName}` : base
  })()
  const { data: currentMonthState } = useCurrentMonth()
  const currentMonthDefined = currentMonthState?.is_defined ?? false
  const isSnapshotView = currentMonthState?.is_snapshot_view ?? false
  const editableMonth = currentMonthDefined && !isSnapshotView
  const snapshots = currentMonthState?.snapshots ?? []
  const [selectedMonth, setSelectedMonth] = useState('')
  const [monthDraft, setMonthDraft] = useState(currentMonth())
  const [nextMonthDraft, setNextMonthDraft] = useState(shiftMonth(currentMonth(), 1))
  const [drillDown, setDrillDown] = useState<DrillDownFilter | null>(null)
  const [editingTx, setEditingTx] = useState<Transaction | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [closeMonthDialogOpen, setCloseMonthDialogOpen] = useState(false)
  const queryClient = useQueryClient()
  const [headerCalOpen, setHeaderCalOpen] = useState(false)
  const [hoveredDay, setHoveredDay] = useState<number | null>(null)
  const dateFnsLocale = i18n.language === 'pt-BR' ? ptBR : enUS
  const effectiveSelectedMonth =
    selectedMonth ||
    currentMonthState?.selected_period ||
    currentMonthState?.current_period ||
    currentMonth()
  const monthParam = `${effectiveSelectedMonth}-01`
  const monthStart = `${effectiveSelectedMonth}-01`
  const monthEnd = `${effectiveSelectedMonth}-${String(monthLastDay(effectiveSelectedMonth)).padStart(2, '0')}`
  const monthLabelStr = monthLabel(effectiveSelectedMonth, locale)
  const hasReadableSelectedPeriod = Boolean(effectiveSelectedMonth) && (currentMonthDefined || isSnapshotView)

  useEffect(() => {
    if (currentMonthState?.selected_period) {
      setSelectedMonth(currentMonthState.selected_period)
    } else if (currentMonthState?.current_period) {
      setSelectedMonth(currentMonthState.current_period)
    }
    if (currentMonthState?.current_period) {
      setMonthDraft(currentMonthState.current_period)
      setNextMonthDraft(shiftMonth(currentMonthState.current_period, 1))
    }
  }, [currentMonthState?.current_period, currentMonthState?.selected_period])

  const handleMonthChange = (newMonth: string) => {
    setSelectedMonth(newMonth)
  }

  const saveCurrentMonthMutation = useMutation({
    mutationFn: (period: string) => months.setCurrent(period),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['current-month'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      setSelectedMonth(data.current_period ?? '')
      setMonthDraft(data.current_period ?? currentMonth())
      toast.success(t('dashboard.currentMonthSaved'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const setMonthViewMutation = useMutation({
    mutationFn: ({ mode, period }: { mode: 'current' | 'snapshot'; period?: string }) =>
      months.setView(mode, period),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['current-month'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setSelectedMonth(data.selected_period ?? '')
      toast.success(
        data.selected_mode === 'snapshot'
          ? t('dashboard.snapshotViewActivated', { period: data.selected_period_label ?? data.selected_period })
          : data.current_period
            ? t('dashboard.returnedToCurrentMonth', { period: data.current_period_label ?? data.current_period })
            : t('dashboard.returnedToMonthSetupToast')
      )
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const closeCurrentMonthMutation = useMutation({
    mutationFn: (nextPeriod: string) => months.closeCurrent(nextPeriod),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['current-month'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setCloseMonthDialogOpen(false)
      setSelectedMonth(data.state.selected_period ?? '')
      setMonthDraft(data.state.current_period ?? currentMonth())
      setNextMonthDraft(shiftMonth(data.state.current_period ?? currentMonth(), 1))
      toast.success(
        t('dashboard.closeMonthSuccess', {
          closed: data.closed_snapshot.period_label,
          next: data.state.current_period_label ?? data.state.current_period,
        })
      )
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['dashboard', 'summary', effectiveSelectedMonth],
    queryFn: () => dashboard.summary(monthParam),
    enabled: hasReadableSelectedPeriod && !!monthParam,
  })

  const { data: spending, isLoading: spendingLoading } = useQuery({
    queryKey: ['dashboard', 'spending', effectiveSelectedMonth],
    queryFn: () => dashboard.spendingByCategory(monthParam),
    enabled: hasReadableSelectedPeriod && !!monthParam,
  })

  const prevMonth = shiftMonth(effectiveSelectedMonth, -1)

  const { data: balanceHistory, isLoading: balanceHistoryLoading } = useQuery({
    queryKey: ['dashboard', 'balance-history', effectiveSelectedMonth],
    queryFn: () => dashboard.balanceHistory(monthParam),
    enabled: hasReadableSelectedPeriod && !!monthParam,
  })

  const { data: currentMonthTxs, isLoading: currentTxLoading } = useQuery({
    queryKey: ['transactions', 'cumulative', effectiveSelectedMonth],
    queryFn: () => transactions.list({
      from: `${effectiveSelectedMonth}-01`,
      to: `${effectiveSelectedMonth}-${String(monthLastDay(effectiveSelectedMonth)).padStart(2, '0')}`,
      limit: 500,
    }),
    enabled: hasReadableSelectedPeriod && !!effectiveSelectedMonth,
  })

  const { data: projectedTxs, isLoading: projectedTxLoading } = useQuery({
    queryKey: ['dashboard', 'projected-transactions', effectiveSelectedMonth],
    queryFn: () => dashboard.projectedTransactions(monthParam),
    enabled: hasReadableSelectedPeriod && !!monthParam,
  })

  const { data: budgetComparison } = useQuery({
    queryKey: ['budgets', 'comparison', effectiveSelectedMonth],
    queryFn: () => budgets.comparison(monthParam),
    enabled: hasReadableSelectedPeriod && !!monthParam,
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Transaction> & { id: string }) =>
      transactions.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      queryClient.invalidateQueries({ queryKey: ['drill-down'] })
      setDialogOpen(false)
      setEditingTx(null)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => transactions.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['transactions'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      queryClient.invalidateQueries({ queryKey: ['drill-down'] })
      setDialogOpen(false)
      setEditingTx(null)
    },
  })


  const cumulativeData = useMemo(() => {
    if (!balanceHistory) return []
    const daysInMonth = monthLastDay(effectiveSelectedMonth)
    const result: { day: number; current: number | null; previous: number }[] = []
    let lastPrevBalance = 0
    for (let day = 1; day <= daysInMonth; day++) {
      const cur = balanceHistory.current.find(d => d.day === day)
      const prev = balanceHistory.previous.find(d => d.day === day)
      if (prev?.balance != null) {
        lastPrevBalance = prev.balance
      }
      result.push({
        day,
        current: cur?.balance ?? null,
        previous: prev?.balance ?? lastPrevBalance,
      })
    }
    return result
  }, [balanceHistory, effectiveSelectedMonth])

  const lastCurrentPoint = [...cumulativeData].reverse().find(d => d.current !== null)
  const lastDay = lastCurrentPoint?.day ?? 0
  const currentStartBalance = balanceHistory?.current.find(d => d.day === 1)?.balance ?? 0
  const currentLatestBalance = lastCurrentPoint?.current ?? 0
  const monthVariation = currentLatestBalance - currentStartBalance

  const primaryCurrency = summary?.primary_currency ?? userCurrency
  const totalBalance = summary?.total_balance_primary ?? Object.values(summary?.total_balance ?? {}).reduce((a, b) => a + Number(b), 0)


  // Savings rate & projection
  const income = Number(summary?.monthly_income_primary ?? summary?.monthly_income ?? 0)
  const expenses = Number(summary?.monthly_expenses_primary ?? summary?.monthly_expenses ?? 0)
  const savingsRate = income > 0 ? ((income - expenses) / income) * 100 : 0
  const isCurrentMonth = effectiveSelectedMonth === currentMonth()
  const daysElapsed = isCurrentMonth ? new Date().getDate() : monthLastDay(effectiveSelectedMonth)
  const daysInMonth = monthLastDay(effectiveSelectedMonth)
  const projectedSpend = expenses > 0 && isCurrentMonth && daysElapsed > 0
    ? (expenses / daysElapsed) * daysInMonth
    : null

  // Uncategorized data
  const uncategorizedCount = summary?.pending_categorization ?? 0
  const uncategorizedAmount = summary?.pending_categorization_amount ?? 0

  const selectedPeriodLabel = currentMonthState?.selected_period_label ?? monthLabel(effectiveSelectedMonth, locale)
  const currentPeriodLabel = currentMonthState?.current_period_label ?? null
  const selectedSnapshotPeriod = currentMonthState?.selected_mode === 'snapshot' ? currentMonthState.selected_period : ''

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
        const actual = s.total
        const prevAmount = budget ? Number(budget.prev_month_amount) : 0
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
      .sort((a, b) => b.actual - a.actual)
  }, [spending, budgetComparison])

  const [txPage, setTxPage] = useState(1)
  useEffect(() => setTxPage(1), [effectiveSelectedMonth])

  type DisplayRow = {
    key: string
    description: string
    date: string
    type: 'debit' | 'credit'
    amount: number
    amountPrimary: number | null
    currency: string
    categoryIcon: string | null
    categoryName: string | null
    categoryColor: string | null
    isProjected: boolean
    attachmentCount: number
  }

  const TX_PER_PAGE = 10
  const allDisplayRows = useMemo(() => {
    const rows: DisplayRow[] = []
    for (const tx of currentMonthTxs?.items ?? []) {
      rows.push({
        key: tx.id,
        description: tx.description,
        date: tx.date,
        type: tx.type,
        amount: Number(tx.amount),
        amountPrimary: tx.amount_primary != null ? Number(tx.amount_primary) : null,
        currency: tx.currency,
        categoryIcon: tx.category?.icon ?? null,
        categoryName: tx.category?.name ?? null,
        categoryColor: tx.category?.color ?? null,
        isProjected: false,
        attachmentCount: tx.attachment_count ?? 0,
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
        isProjected: true,
        attachmentCount: 0,
      })
    }
    rows.sort((a, b) => a.date.localeCompare(b.date))
    return rows
  }, [currentMonthTxs, projectedTxs])

  const txTotalPages = Math.ceil(allDisplayRows.length / TX_PER_PAGE)
  const pagedRows = allDisplayRows.slice((txPage - 1) * TX_PER_PAGE, txPage * TX_PER_PAGE)
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

  if (!currentMonthDefined && !isSnapshotView) {
    return (
      <div className="space-y-6">
        <PageHeader section={t('dashboard.title')} title={greeting} />

        <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div className="space-y-1">
              <p className="text-sm font-semibold text-foreground">{t('dashboard.currentMonthTitle')}</p>
              <p className="text-sm text-muted-foreground">{t('dashboard.currentMonthUndefined')}</p>
              <p className="text-xs text-muted-foreground">{t('dashboard.currentMonthGuardHint')}</p>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-end">
              <DatePickerInput
                value={monthDraft}
                onChange={setMonthDraft}
                mode="month"
                className="w-full sm:w-[220px] justify-start"
              />
              <Button
                onClick={() => saveCurrentMonthMutation.mutate(monthDraft)}
                disabled={!monthDraft || saveCurrentMonthMutation.isPending}
              >
                {saveCurrentMonthMutation.isPending ? t('common.loading') : t('dashboard.defineCurrentMonth')}
              </Button>
              {snapshots.length > 0 ? (
                <select
                  className="h-10 min-w-[220px] rounded-lg border border-border bg-card px-3 text-sm text-foreground"
                  defaultValue=""
                  onChange={(event) => {
                    const nextValue = event.target.value
                    if (!nextValue) return
                    const snapshot = snapshots.find((item) => item.period === nextValue)
                    if (!snapshot) return
                    const confirmed = window.confirm(
                      t('dashboard.snapshotConfirmMessage', { period: snapshot.period_label })
                    )
                    if (!confirmed) return
                    setMonthViewMutation.mutate({ mode: 'snapshot', period: snapshot.period })
                  }}
                  disabled={setMonthViewMutation.isPending}
                >
                  <option value="">{t('dashboard.browseClosedMonths')}</option>
                  {snapshots.map((snapshot) => (
                    <option key={snapshot.period} value={snapshot.period}>
                      {t('dashboard.snapshotOption', { period: snapshot.period_label })}
                    </option>
                  ))}
                </select>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <PageHeader
        section={greeting}
        title={new Date(effectiveSelectedMonth + '-02').toLocaleDateString(locale, { month: 'long', year: 'numeric' }).replace(/^\w/, c => c.toUpperCase())}
        action={
          <div className="flex items-center gap-1">
            <button
              className="h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:border-border hover:text-foreground transition-all text-base"
              onClick={() => handleMonthChange(shiftMonth(effectiveSelectedMonth, -1))}
            >&#8249;</button>
            <Popover open={headerCalOpen} onOpenChange={setHeaderCalOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className="inline-flex items-center gap-2 border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground hover:bg-muted/50 transition-all cursor-pointer"
                >
                  <CalendarIcon className="size-3.5 text-muted-foreground" />
                  {new Date(effectiveSelectedMonth + '-02').toLocaleDateString(locale, { month: 'long', year: 'numeric' }).replace(/^\w/, c => c.toUpperCase())}
                </button>
              </PopoverTrigger>
              <PopoverContent align="center" className="w-auto p-0">
                <Calendar
                  mode="single"
                  locale={dateFnsLocale}
                  selected={new Date(`${effectiveSelectedMonth}-01T00:00:00`)}
                  defaultMonth={new Date(`${effectiveSelectedMonth}-01T00:00:00`)}
                  onSelect={(date) => {
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
              onClick={() => handleMonthChange(shiftMonth(effectiveSelectedMonth, 1))}
            >&#8250;</button>
          </div>
        }
      />

      <div className="mb-5 rounded-xl border border-border bg-card shadow-sm">
        <div className="flex flex-col gap-4 px-5 py-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-3">
            <div className="space-y-1">
              <p className="text-sm font-semibold text-foreground">{t('dashboard.currentMonthTitle')}</p>
              <p className="text-sm text-muted-foreground">
                {isSnapshotView
                  ? t('dashboard.snapshotViewDescription', { period: selectedPeriodLabel })
                  : t('dashboard.currentMonthActiveDescription', { period: currentPeriodLabel })}
              </p>
            </div>

            {isSnapshotView ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                <p className="font-medium">{t('dashboard.snapshotViewBadge', { period: selectedPeriodLabel })}</p>
                <p className="mt-1 text-amber-800">{t('dashboard.snapshotReadOnlyHint')}</p>
              </div>
            ) : null}

            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-end">
              <select
                id="month-view-select"
                className="h-10 min-w-[220px] rounded-lg border border-border bg-card px-3 text-sm text-foreground"
                value={selectedSnapshotPeriod || '__current__'}
                onChange={(event) => {
                  const nextValue = event.target.value
                  if (nextValue === '__current__') {
                    setMonthViewMutation.mutate({ mode: 'current' })
                    return
                  }
                  const snapshot = snapshots.find((item) => item.period === nextValue)
                  if (!snapshot) return
                  const confirmed = window.confirm(
                    t('dashboard.snapshotConfirmMessage', { period: snapshot.period_label })
                  )
                  if (!confirmed) return
                  setMonthViewMutation.mutate({ mode: 'snapshot', period: snapshot.period })
                }}
                disabled={setMonthViewMutation.isPending}
              >
                {currentMonthDefined ? (
                  <option value="__current__">
                    {t('dashboard.currentMonthOption', { period: currentPeriodLabel })}
                  </option>
                ) : (
                  <option value="__current__">{t('dashboard.monthSetupOption')}</option>
                )}
                {snapshots.map((snapshot) => (
                  <option key={snapshot.period} value={snapshot.period}>
                    {t('dashboard.snapshotOption', { period: snapshot.period_label })}
                  </option>
                ))}
              </select>

              {isSnapshotView ? (
                <Button
                  variant="outline"
                  onClick={() => setMonthViewMutation.mutate({ mode: 'current' })}
                  disabled={setMonthViewMutation.isPending}
                >
                  {currentMonthDefined ? t('dashboard.returnToCurrentMonth') : t('dashboard.returnToMonthSetup')}
                </Button>
              ) : null}
            </div>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-end">
            <Button
              variant="outline"
              onClick={() => setCloseMonthDialogOpen(true)}
              disabled={!editableMonth || closeCurrentMonthMutation.isPending}
              title={isSnapshotView ? t('dashboard.snapshotCloseDisabled') : undefined}
            >
              {closeCurrentMonthMutation.isPending ? t('common.loading') : t('dashboard.closeCurrentMonth')}
            </Button>
          </div>
        </div>
      </div>

      {/* Hero Card: Savings Rate + Uncategorized CTA */}
      <div className="bg-card rounded-xl border border-border shadow-sm mb-5">
        <div className="grid grid-cols-1 lg:grid-cols-3">
          {/* Left: Savings Rate & Metrics */}
          <div className="lg:col-span-2 px-5 py-4">
            <div className="flex items-baseline gap-3 mb-3">
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-0.5">{t('dashboard.savingsRate')}</p>
                {summaryLoading ? (
                  <Skeleton className="h-10 w-28" />
                ) : (
                  <p className={`text-4xl font-bold tabular-nums leading-tight ${savingsRateColor}`}>
                    {savingsRateDisplay}
                  </p>
                )}
              </div>
            </div>

            <div className="flex flex-wrap gap-6">
              {/* Balance */}
              <div className="min-w-0">
                <p className="text-xs font-medium text-muted-foreground mb-0.5">{t('dashboard.totalBalance')}</p>
                {summaryLoading ? (
                  <Skeleton className="h-7 w-24" />
                ) : (
                  <div>
                    <p className={`text-lg font-bold tabular-nums ${totalBalance < 0 ? 'text-rose-500' : 'text-foreground'}`}>
                      {mask(formatCurrency(totalBalance, primaryCurrency, locale))}
                    </p>
                    {/* Per-currency breakdown when multiple currencies */}
                    {summary?.total_balance && Object.keys(summary.total_balance).length > 1 && (
                      <div className="flex flex-wrap gap-x-2 mt-0.5">
                        {Object.entries(summary.total_balance).map(([cur, val]) => (
                          <span key={cur} className="text-[10px] text-muted-foreground tabular-nums">
                            {mask(formatCurrency(val, cur, locale))}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Income */}
              <div
                className="min-w-0 cursor-pointer hover:opacity-70 transition-opacity"
                onClick={() => setDrillDown({
                  title: t('dashboard.drillDownIncome', { month: monthLabelStr }),
                  type: 'credit',
                  from: monthStart,
                  to: monthEnd,
                })}
              >
                <p className="text-xs font-medium text-muted-foreground mb-0.5">{t('dashboard.monthlyIncome')}</p>
                {summaryLoading ? (
                  <Skeleton className="h-7 w-24" />
                ) : (
                  <p className="text-lg font-bold tabular-nums text-emerald-600">
                    +{mask(formatCurrency(income, primaryCurrency, locale))}
                  </p>
                )}
              </div>

              {/* Expenses */}
              <div
                className="min-w-0 cursor-pointer hover:opacity-70 transition-opacity"
                onClick={() => setDrillDown({
                  title: t('dashboard.drillDownExpenses', { month: monthLabelStr }),
                  type: 'debit',
                  from: monthStart,
                  to: monthEnd,
                })}
              >
                <p className="text-xs font-medium text-muted-foreground mb-0.5">{t('dashboard.monthlyExpenses')}</p>
                {summaryLoading ? (
                  <Skeleton className="h-7 w-24" />
                ) : (
                  <p className="text-lg font-bold tabular-nums text-rose-500">
                    -{mask(formatCurrency(expenses, primaryCurrency, locale))}
                  </p>
                )}
              </div>

              {/* Assets Value */}
              {!summaryLoading && summary?.assets_value && Object.values(summary.assets_value).reduce((a, b) => a + b, 0) > 0 && (
                <div className="min-w-0">
                  <p className="text-xs font-medium text-muted-foreground mb-0.5">{t('dashboard.assetsValue')}</p>
                  <p className="text-lg font-bold tabular-nums text-blue-600">
                    {mask(formatCurrency(summary.assets_value_primary ?? Object.values(summary.assets_value).reduce((a, b) => a + b, 0), primaryCurrency, locale))}
                  </p>
                </div>
              )}
            </div>

            {/* Spending projection */}
            {projectedSpend !== null && !summaryLoading && (
              <p className="text-xs text-muted-foreground mt-2">
                {t('dashboard.spendingProjection', { amount: mask(formatCurrency(projectedSpend, primaryCurrency, locale)) })}
              </p>
            )}
          </div>

          {/* Right: Uncategorized CTA */}
          <div className="lg:col-span-1 px-5 py-4 border-t lg:border-t-0 lg:border-l border-border flex flex-col items-center justify-center text-center">
            {summaryLoading ? (
              <Skeleton className="h-16 w-16 rounded-full" />
            ) : uncategorizedCount > 0 ? (
              <div
                className="cursor-pointer hover:opacity-80 transition-opacity"
                onClick={() => setDrillDown({
                  title: t('dashboard.drillDownUncategorized'),
                  uncategorized: true,
                })}
              >
                <div className={`w-14 h-14 rounded-full flex items-center justify-center text-xl font-bold text-white mx-auto mb-2 ${
                  uncategorizedCount >= 20 ? 'bg-amber-500' : 'bg-amber-400'
                }`}>
                  {uncategorizedCount}
                </div>
                <p className="text-sm font-medium text-foreground">
                  {t('dashboard.uncategorizedCta', { count: uncategorizedCount })}
                </p>
                {uncategorizedAmount > 0 && (
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {mask(t('dashboard.uncategorizedTotal', { amount: formatCurrency(uncategorizedAmount, userCurrency, locale) }))}
                  </p>
                )}
                <p className="text-sm font-semibold text-amber-600 mt-2 hover:underline">
                  {t('dashboard.categorizeNow')} &rarr;
                </p>
              </div>
            ) : (
              <div>
                <CheckCircle2 className="w-10 h-10 text-emerald-500 mx-auto mb-1.5" />
                <p className="text-sm font-semibold text-foreground">{t('dashboard.allCategorized')}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{t('dashboard.allCategorizedDesc')}</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Charts: Category Spending Bars + Balance Flow */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mb-5" style={{ gridAutoRows: 'minmax(380px, auto)' }}>
        {/* Category Spending Bars */}
        <div className="bg-card rounded-xl border border-border shadow-sm flex flex-col max-h-[420px]">
          <div className="px-5 py-4 border-b border-border shrink-0">
            <p className="text-sm font-semibold text-foreground">{t('dashboard.spendingByCategory')}</p>
          </div>
          <div className="p-3 overflow-y-auto flex-1">
            {spendingLoading ? (
              <div className="space-y-3 p-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : mergedCategories.length > 0 ? (
              <div className="space-y-1.5">
                {mergedCategories.map((item) => {
                  const hasBudget = item.budget_amount != null && item.budget_amount > 0
                  const pct = item.percentage_used
                  const barColor = hasBudget
                    ? pct! > 100 ? 'bg-rose-500' : pct! >= 80 ? 'bg-amber-400' : 'bg-emerald-500'
                    : 'bg-muted-foreground/20'

                  return (
                    <div
                      key={item.category_id}
                      className="rounded-lg px-3 py-2.5 hover:bg-muted/50 transition-colors cursor-pointer"
                      onClick={() => setDrillDown({
                        title: t('dashboard.drillDownCategory', { category: item.category_name, month: monthLabelStr }),
                        category_id: item.category_id,
                        type: 'debit',
                        from: monthStart,
                        to: monthEnd,
                      })}
                    >
                      <div className="flex items-center gap-3">
                        <CategoryIcon icon={item.category_icon} color={item.category_color} size="lg" />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between gap-2 mb-1">
                            <span className="text-sm font-semibold text-foreground truncate">{item.category_name}</span>
                            <div className="flex items-center gap-2 shrink-0">
                              <span className="text-sm font-bold tabular-nums text-foreground">{mask(formatCurrency(item.actual, userCurrency, locale))}</span>
                              {item.momPct !== null && (
                                <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-bold tabular-nums ${
                                  item.momPct > 0 ? 'bg-rose-100 text-rose-600 dark:bg-rose-500/20 dark:text-rose-400' : item.momPct < 0 ? 'bg-emerald-100 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-400' : 'bg-muted text-muted-foreground'
                                }`}>
                                  {item.momPct > 0 ? '\u2191' : item.momPct < 0 ? '\u2193' : '='}{Math.abs(item.momPct).toFixed(0)}%
                                </span>
                              )}
                            </div>
                          </div>
                          {hasBudget && (
                            <div className="flex items-center gap-2">
                              <div className="flex-1 h-1.5 bg-muted/60 rounded-full overflow-hidden">
                                <div
                                  className={`h-full rounded-full transition-all ${barColor}`}
                                  style={{ width: `${Math.min(pct!, 100)}%` }}
                                />
                              </div>
                              <span className={`text-[11px] tabular-nums font-medium shrink-0 ${
                                pct! > 100 ? 'text-rose-500' : pct! >= 80 ? 'text-amber-500' : 'text-muted-foreground'
                              }`}>
                                {mask(t('dashboard.ofBudget', { budget: formatCurrency(item.budget_amount!, userCurrency, locale) }))}
                              </span>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <p className="text-muted-foreground text-sm text-center py-12">{t('dashboard.noData')}</p>
            )}
          </div>
        </div>

        {/* Cumulative Spending Comparison */}
        <div className="bg-card rounded-xl border border-border shadow-sm max-h-[420px] flex flex-col">
          <div className="px-5 pt-5 pb-3 shrink-0">
            <div className="flex items-start justify-between mb-0.5">
              <div>
                <p className="text-base font-bold text-foreground">{t('dashboard.balanceFlow')}</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {new Date(`${effectiveSelectedMonth}-01T00:00:00`).toLocaleDateString(locale)} → {new Date(`${effectiveSelectedMonth}-${String(lastCurrentPoint?.day ?? monthLastDay(effectiveSelectedMonth)).padStart(2, '0')}T00:00:00`).toLocaleDateString(locale)}
                </p>
              </div>
              {!balanceHistoryLoading && lastCurrentPoint && (
                <span className={`text-lg font-bold tabular-nums ${monthVariation >= 0 ? 'text-emerald-600' : 'text-rose-500'}`}>
                  {mask(`${monthVariation > 0 ? '+' : ''}${formatCurrency(monthVariation, userCurrency, locale)}`)}
                </span>
              )}
            </div>
          </div>
          <div className="px-1 pb-4 flex-1 min-h-0">
            {balanceHistoryLoading ? (
              <Skeleton className="h-full w-full" />
            ) : cumulativeData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart
                  data={cumulativeData}
                  margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
                  className="cursor-pointer"
                  onMouseMove={(state) => {
                    const idx = state?.activeTooltipIndex
                    if (typeof idx === 'number') {
                      const point = cumulativeData[idx]
                      if (point) setHoveredDay(point.day)
                    }
                  }}
                  onMouseLeave={() => setHoveredDay(null)}
                  onClick={(_state) => {
                    // Access activePayload from the underlying native event target chart state
                    const chartState = _state as unknown as { activePayload?: Array<{ payload: { day: number } }> }
                    const payload = chartState?.activePayload ?? []
                    if (payload[0]) {
                      const day = String(payload[0].payload.day).padStart(2, '0')
                      const dateStr = `${effectiveSelectedMonth}-${day}`
                      setDrillDown({
                        title: t('dashboard.drillDownDay', { date: new Date(dateStr + 'T00:00:00').toLocaleDateString(locale) }),
                        from: dateStr,
                        to: dateStr,
                      })
                    }
                  }}
                >
                  <defs>
                    <linearGradient id="cumGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#10B981" stopOpacity={0.18} />
                      <stop offset="95%" stopColor="#10B981" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <XAxis
                    dataKey="day"
                    tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                    axisLine={false}
                    tickLine={false}
                    interval={3}
                  />
                  <YAxis
                    tickFormatter={(v) => {
                      if (privacyMode) return ''
                      if (v === 0) return '0'
                      return formatCurrency(v, userCurrency, locale).replace(/,00$/, '').replace(/\.00$/, '')
                    }}
                    tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                    axisLine={false}
                    tickLine={false}
                    width={56}
                    tickCount={5}
                    domain={[
                      (dataMin: number) => dataMin < 0 ? Math.floor(dataMin / 100) * 100 : 0,
                      (dataMax: number) => Math.ceil(dataMax / 100) * 100,
                    ]}
                  />
                  <Tooltip
                    formatter={(value, name) => [
                      value !== null ? (privacyMode ? MASK : formatCurrency(Number(value), userCurrency, locale)) : '\u2014',
                      name === 'current' ? monthLabel(effectiveSelectedMonth, locale).split(' ')[0] : monthLabel(prevMonth, locale).split(' ')[0],
                    ]}
                    labelFormatter={(day) => t('dashboard.day', { day })}
                    contentStyle={{
                      background: 'var(--card)',
                      color: 'var(--foreground)',
                      border: '1px solid var(--border)',
                      borderRadius: '0.75rem',
                      boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
                      fontSize: '12px',
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="current"
                    stroke="#10B981"
                    strokeWidth={2}
                    fill="url(#cumGrad)"
                    dot={false}
                    activeDot={{ r: 3, fill: '#10B981' }}
                    connectNulls={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="previous"
                    stroke="#94A3B8"
                    strokeWidth={2}
                    strokeDasharray="5 3"
                    dot={false}
                    activeDot={{ r: 3, fill: '#94A3B8' }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-muted-foreground text-sm text-center py-12">{t('dashboard.noData')}</p>
            )}
          </div>
          {!balanceHistoryLoading && lastCurrentPoint && (() => {
            const footerDay = hoveredDay ?? lastDay
            const footerPrev = balanceHistory?.previous.find(d => d.day === footerDay)?.balance ?? 0
            const footerCurrent = cumulativeData.find(d => d.day === footerDay)?.current ?? totalBalance
            const footerPct = footerPrev !== 0 ? ((footerCurrent - footerPrev) / Math.abs(footerPrev)) * 100 : null
            if (footerPrev === 0 || footerPct === null) return null
            return (
              <div className="px-5 pb-4 pt-0 shrink-0">
                <p className="text-xs text-muted-foreground">
                  {t('dashboard.balanceFlowVsPrev', {
                    month: monthLabel(prevMonth, locale).split(' ')[0],
                    day: footerDay,
                    amount: mask(formatCurrency(footerPrev, userCurrency, locale)),
                    delta: `${footerPct >= 0 ? '+' : ''}${footerPct.toFixed(1)}%`,
                  })}
                  {' '}
                  <span className={footerPct >= 0 ? 'text-emerald-600' : 'text-rose-500'}>
                    {footerPct >= 0 ? '\u25B2' : '\u25BC'}
                  </span>
                </p>
              </div>
            )
          })()}
        </div>
      </div>

      {/* Period Transactions */}
      <div>
        <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
          <div className="px-5 py-4 border-b border-border">
            <p className="text-sm font-semibold text-foreground">{t('dashboard.periodTransactions')}</p>
          </div>
          {txListLoading ? (
            <div className="p-5 space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : pagedRows.length > 0 ? (
            <>
              <Table>
                <TableHeader>
                  <TableRow className="border-b border-border hover:bg-transparent">
                    <TableHead className="pl-5 text-xs font-medium text-muted-foreground">{t('transactions.description')}</TableHead>
                    <TableHead className="pr-5 text-right text-xs font-medium text-muted-foreground">{t('transactions.amount')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pagedRows.map((row) => (
                    <TableRow
                      key={row.key}
                      className={`border-b border-border last:border-0 ${!row.isProjected ? 'cursor-pointer hover:bg-muted' : ''}`}
                      onClick={() => {
                        if (row.isProjected) return
                        const tx = currentMonthTxs?.items.find((t) => t.id === row.key)
                        if (tx) { setEditingTx(tx); setDialogOpen(true) }
                      }}
                    >
                      <TableCell className="py-2.5 pl-5">
                        <div className="flex items-center gap-3">
                          <CategoryIcon icon={row.categoryIcon} color={row.categoryColor} size="lg" />
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <p className="text-sm font-semibold text-foreground truncate">{row.description}</p>
                              {row.isProjected && (
                                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-violet-100 text-violet-600 shrink-0">
                                  {t('transactions.recurringBadge')}
                                </span>
                              )}
                              {row.attachmentCount > 0 && (
                                <Paperclip size={12} className="text-muted-foreground shrink-0" />
                              )}
                            </div>
                            <p className="text-xs text-muted-foreground">{formatDate(row.date, locale)}</p>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="py-2.5 pr-5 text-right">
                        <span className={`text-sm font-semibold tabular-nums ${row.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
                          {mask(`${row.type === 'credit' ? '+' : '-'}${formatCurrency(Math.abs(row.amount), row.currency, locale)}`)}
                        </span>
                        {row.currency !== userCurrency && row.amountPrimary != null && (
                          <span className="block text-[10px] text-muted-foreground tabular-nums">
                            {mask(formatCurrency(Math.abs(row.amountPrimary), userCurrency, locale))}
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {txTotalPages > 1 && (
                <div className="flex items-center justify-center gap-2 py-4 border-t border-border">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={txPage <= 1}
                    onClick={() => setTxPage(txPage - 1)}
                  >
                    {t('dashboard.previous')}
                  </Button>
                  <span className="text-sm text-muted-foreground">
                    {txPage} / {txTotalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={txPage >= txTotalPages}
                    onClick={() => setTxPage(txPage + 1)}
                  >
                    {t('dashboard.next')}
                  </Button>
                </div>
              )}
            </>
          ) : (
            <p className="text-muted-foreground text-sm text-center py-8">{t('dashboard.noTransactions')}</p>
          )}
        </div>
      </div>

      <TransactionDrillDown
        filter={drillDown}
        onClose={() => setDrillDown(null)}
        onTransactionClick={(tx) => { setEditingTx(tx); setDialogOpen(true) }}
      />

      <Dialog open={closeMonthDialogOpen} onOpenChange={setCloseMonthDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('dashboard.closeMonthModalTitle')}</DialogTitle>
            <DialogDescription>
              {t('dashboard.closeMonthModalDescription', { period: currentPeriodLabel ?? selectedPeriodLabel })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <DatePickerInput
              value={nextMonthDraft}
              onChange={setNextMonthDraft}
              mode="month"
              className="w-full justify-start"
              disabled={closeCurrentMonthMutation.isPending}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setCloseMonthDialogOpen(false)}
              disabled={closeCurrentMonthMutation.isPending}
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="button"
              onClick={() => closeCurrentMonthMutation.mutate(nextMonthDraft)}
              disabled={!nextMonthDraft || closeCurrentMonthMutation.isPending}
            >
              {closeCurrentMonthMutation.isPending ? t('common.loading') : t('dashboard.confirmCloseCurrentMonth')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <TransactionDialog
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setEditingTx(null) }}
        transaction={editingTx}
        categories={(categoriesList ?? []).map((c: { id: string; name: string; icon: string }) => ({ id: c.id, name: c.name, icon: c.icon }))}
        accounts={(accountsList ?? []).map((a: { id: string; name: string }) => ({ id: a.id, name: a.name }))}
        onSave={(data) => {
          if (editingTx) updateMutation.mutate({ id: editingTx.id, ...data })
        }}
        onDelete={() => {
          if (editingTx) deleteMutation.mutate(editingTx.id)
        }}
        loading={updateMutation.isPending || deleteMutation.isPending}
        error={updateMutation.error ? extractApiError(updateMutation.error) : deleteMutation.error ? extractApiError(deleteMutation.error) : null}
        isSynced={!!editingTx?.external_id}
      />
    </div>
  )
}
