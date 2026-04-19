import { useState, useMemo, useEffect, useRef } from 'react'
import { getAccountName } from '@/lib/account-utils'
import { useParams, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query'
import { format, addDays, addMonths, parseISO } from 'date-fns'
import { ptBR, enUS } from 'date-fns/locale'
import { accounts, transactions, categories as categoriesApi } from '@/lib/api'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { toast } from 'sonner'
import type { Transaction } from '@/types'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { ArrowLeft, ArrowLeftRight, ChevronLeft, ChevronRight, Clock, HelpCircle, Paperclip, Pencil, X } from 'lucide-react'
import { CategoryIcon } from '@/components/category-icon'
import { TransactionDialog, extractApiError } from '@/components/transaction-dialog'
import { TransferDialog } from '@/components/transfer-dialog'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'

function defaultFrom() {
  const now = new Date()
  return format(new Date(now.getFullYear(), now.getMonth(), 1), 'yyyy-MM-dd')
}

function defaultTo() {
  return format(new Date(), 'yyyy-MM-dd')
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate()
}

/** Return the default cycle for a credit card: the cycle whose bill is *next due*.
 *
 * This is NOT the cycle containing today. When the statement has just closed
 * (e.g. gold: closes day 11, today is day 13, due day 16), the user wants to
 * see the bill they're about to pay (Abr 2026), not the brand-new open cycle
 * that's busy accumulating charges for next month's bill (Mai 2026). For accounts
 * where the close hasn't happened yet (e.g. TASSIO: close 28, today 13) the
 * "next due" cycle IS the open one, so this function returns the same as
 * creditCardCycleBoundaries(closeDay, today). */
function defaultCycleForCreditCard(
  closeDay: number | null | undefined,
  dueDay: number | null | undefined,
  reference: Date,
): { start: string; end: string } {
  if (!closeDay || !dueDay) {
    return creditCardCycleBoundaries(closeDay, reference)
  }
  // Step 1: find the next occurrence of dueDay on or after `reference`.
  const ref0 = new Date(reference)
  ref0.setHours(0, 0, 0, 0)
  const y = ref0.getFullYear()
  const m = ref0.getMonth()
  const clampDue = (yy: number, mm: number) => Math.min(dueDay, daysInMonth(yy, mm))
  const sameMonthDue = new Date(y, m, clampDue(y, m))
  let billDate: Date
  if (sameMonthDue.getTime() >= ref0.getTime()) {
    billDate = sameMonthDue
  } else {
    const ny = m === 11 ? y + 1 : y
    const nm = m === 11 ? 0 : m + 1
    billDate = new Date(ny, nm, clampDue(ny, nm))
  }
  // Step 2: find the cycle whose bill is `billDate` — its close date is the
  // most recent closeDay on or before billDate. The cycle's inclusive last
  // day is one day before that close (per Brazilian convention).
  const by = billDate.getFullYear()
  const bm = billDate.getMonth()
  const clampClose = (yy: number, mm: number) => Math.min(closeDay, daysInMonth(yy, mm))
  const sameMonthClose = new Date(by, bm, clampClose(by, bm))
  let cycleClose: Date
  if (sameMonthClose.getTime() <= billDate.getTime()) {
    cycleClose = sameMonthClose
  } else {
    const py = bm === 0 ? by - 1 : by
    const pm = bm === 0 ? 11 : bm - 1
    cycleClose = new Date(py, pm, clampClose(py, pm))
  }
  const refInsideCycle = new Date(cycleClose)
  refInsideCycle.setDate(refInsideCycle.getDate() - 1)
  return creditCardCycleBoundaries(closeDay, refInsideCycle)
}

/** Compute the bill due date for a credit card cycle whose end is `cycleEnd`.
 * Each bill is due on the next occurrence of `dueDay` strictly after the cycle's
 * statement close. Returns null when dueDay is not configured. */
function dueDateForCycle(cycleEnd: string, dueDay: number | null | undefined): string | null {
  if (!dueDay) return null
  const to = parseISO(cycleEnd + 'T00:00:00')
  const y = to.getFullYear()
  const m = to.getMonth()
  const clamp = (yy: number, mm: number) => Math.min(dueDay, daysInMonth(yy, mm))
  const sameMonth = new Date(y, m, clamp(y, m))
  let bill: Date
  if (sameMonth > to) {
    bill = sameMonth
  } else {
    const ny = m === 11 ? y + 1 : y
    const nm = m === 11 ? 0 : m + 1
    bill = new Date(ny, nm, clamp(ny, nm))
  }
  return format(bill, 'yyyy-MM-dd')
}

/** Build a "Maio 2026"-style label for a credit card cycle.
 * Brazilian convention: the bill is named after the month it's due, which is
 * the next occurrence of payment_due_day strictly after the cycle close. */
function creditCardCycleLabel(
  filterTo: string,
  dueDay: number | null | undefined,
  i18nLanguage: string,
): string {
  const dateFnsLocale = i18nLanguage === 'pt-BR' ? ptBR : enUS
  const to = parseISO(filterTo + 'T00:00:00')
  if (!dueDay) {
    return format(to, 'MMM yyyy', { locale: dateFnsLocale })
  }
  const y = to.getFullYear()
  const m = to.getMonth()
  const clamp = (yy: number, mm: number) => Math.min(dueDay, daysInMonth(yy, mm))
  const sameMonth = new Date(y, m, clamp(y, m))
  let bill: Date
  if (sameMonth > to) {
    bill = sameMonth
  } else {
    const ny = m === 11 ? y + 1 : y
    const nm = m === 11 ? 0 : m + 1
    bill = new Date(ny, nm, clamp(ny, nm))
  }
  return format(bill, 'MMM yyyy', { locale: dateFnsLocale })
}

/** Return the [start, end] dates of the billing cycle that CONTAINS `reference`.
 * Brazilian convention: a transaction ON the close day belongs to the NEXT
 * cycle, so the cycle boundaries are [previous close day, next close day − 1].
 * Falls back to "previous month → today" when no closeDay is configured. */
function creditCardCycleBoundaries(
  closeDay: number | null | undefined,
  reference: Date,
): { start: string; end: string } {
  if (!closeDay) {
    const y = reference.getFullYear()
    const m = reference.getMonth()
    return {
      start: format(new Date(y, m - 1, 1), 'yyyy-MM-dd'),
      end: format(reference, 'yyyy-MM-dd'),
    }
  }
  const ref0 = new Date(reference)
  ref0.setHours(0, 0, 0, 0)
  const y = ref0.getFullYear()
  const m = ref0.getMonth()
  const clamp = (yy: number, mm: number) => Math.min(closeDay, daysInMonth(yy, mm))
  // The cycle containing `reference` ends the day before the next close date
  // strictly after `reference`.
  const thisMonthClose = new Date(y, m, clamp(y, m))
  let nextClose: Date
  if (thisMonthClose.getTime() > ref0.getTime()) {
    nextClose = thisMonthClose
  } else {
    const nextY = m === 11 ? y + 1 : y
    const nextM = m === 11 ? 0 : m + 1
    nextClose = new Date(nextY, nextM, clamp(nextY, nextM))
  }
  const end = new Date(nextClose)
  end.setDate(end.getDate() - 1)
  // Start = the previous close day (the close day itself opens a new cycle).
  const prevY = nextClose.getMonth() === 0 ? nextClose.getFullYear() - 1 : nextClose.getFullYear()
  const prevM = nextClose.getMonth() === 0 ? 11 : nextClose.getMonth() - 1
  const start = new Date(prevY, prevM, clamp(prevY, prevM))
  return {
    start: format(start, 'yyyy-MM-dd'),
    end: format(end, 'yyyy-MM-dd'),
  }
}

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

function formatDateStr(dateStr: string, locale = 'pt-BR') {
  return new Date(dateStr + 'T00:00:00').toLocaleDateString(locale)
}

function formatFriendlyDate(dateStr: string, i18nLanguage: string) {
  const dateFnsLocale = i18nLanguage === 'pt-BR' ? ptBR : enUS
  const d = parseISO(dateStr + 'T00:00:00')
  // Compact friendly format: pt-BR → "qui, 16 abr" / en → "Thu, Apr 16"
  return i18nLanguage === 'pt-BR'
    ? format(d, 'EEE, d MMM', { locale: dateFnsLocale })
    : format(d, 'EEE, MMM d', { locale: dateFnsLocale })
}

function daysUntil(dateStr: string): number {
  const target = new Date(dateStr + 'T00:00:00')
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((target.getTime() - today.getTime()) / (1000 * 60 * 60 * 24))
}

function utilizationColor(pct: number): string {
  if (pct >= 90) return 'bg-rose-500'
  if (pct >= 70) return 'bg-amber-400'
  if (pct >= 30) return 'bg-blue-500'
  return 'bg-emerald-500'
}

type TxWithBalance = Transaction & { runningBalance: number }

export default function AccountDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { t, i18n } = useTranslation()
  const { mask, privacyMode, MASK } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingTx, setEditingTx] = useState<Transaction | null>(null)
  const [transferDialogOpen, setTransferDialogOpen] = useState(false)
  const [filterFrom, setFilterFrom] = useState(defaultFrom)
  const [filterTo, setFilterTo] = useState(defaultTo)
  const [showPrimary, setShowPrimary] = useState(false)
  const filterTouched = useRef(false)
  const handleFilterFromChange = (v: string) => { filterTouched.current = true; setFilterFrom(v) }
  const handleFilterToChange = (v: string) => { filterTouched.current = true; setFilterTo(v) }
  const shiftCycleBy = (direction: -1 | 1) => {
    filterTouched.current = true
    if (account?.type === 'credit_card' && account?.statement_close_day) {
      const ref = direction === -1
        ? new Date(parseISO(filterFrom + 'T00:00:00').getTime() - 86400000)
        : new Date(parseISO(filterTo + 'T00:00:00').getTime() + 86400000)
      const { start, end } = creditCardCycleBoundaries(account.statement_close_day, ref)
      setFilterFrom(start)
      setFilterTo(end)
      return
    }
    setFilterFrom(format(addMonths(parseISO(filterFrom + 'T00:00:00'), direction), 'yyyy-MM-dd'))
    setFilterTo(format(addMonths(parseISO(filterTo + 'T00:00:00'), direction), 'yyyy-MM-dd'))
  }

  const { data: account, isLoading: accountLoading } = useQuery({
    queryKey: ['accounts', id],
    queryFn: () => accounts.get(id!),
    enabled: !!id,
  })

  useEffect(() => {
    if (!account || filterTouched.current) return
    if (account.type === 'credit_card') {
      const { start, end } = defaultCycleForCreditCard(
        account.statement_close_day,
        account.payment_due_day,
        new Date(),
      )
      setFilterFrom(start)
      setFilterTo(end)
    }
  }, [account])

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accounts.list(),
  })

  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['accounts', id, 'summary', filterFrom, filterTo],
    queryFn: () => accounts.summary(id!, filterFrom || undefined, filterTo || undefined),
    enabled: !!id,
  })

  // Previous cycle (for the Total da fatura comparison subtitle).
  // Only fires for credit cards with a statement_close_day set.
  const previousCycle = useMemo(() => {
    if (!account || account.type !== 'credit_card' || !account.statement_close_day) return null
    const dayBeforeStart = new Date(parseISO(filterFrom + 'T00:00:00').getTime() - 86400000)
    return creditCardCycleBoundaries(account.statement_close_day, dayBeforeStart)
  }, [account, filterFrom])

  const { data: previousCycleSummary } = useQuery({
    queryKey: ['accounts', id, 'summary', previousCycle?.start, previousCycle?.end],
    queryFn: () => accounts.summary(id!, previousCycle!.start, previousCycle!.end),
    enabled: !!id && !!previousCycle,
  })

  // Last 6 cycles (oldest → newest) for the bill timeline strip.
  const timelineCycles = useMemo(() => {
    if (!account || account.type !== 'credit_card' || !account.statement_close_day) return []
    const cycles: { start: string; end: string }[] = []
    let ref = new Date()
    for (let i = 0; i < 6; i++) {
      const c = creditCardCycleBoundaries(account.statement_close_day, ref)
      cycles.unshift(c)
      ref = new Date(parseISO(c.start + 'T00:00:00').getTime() - 86400000)
    }
    return cycles
  }, [account])

  const timelineQueries = useQueries({
    queries: timelineCycles.map(c => ({
      queryKey: ['accounts', id, 'summary', c.start, c.end],
      queryFn: () => accounts.summary(id!, c.start, c.end),
      enabled: !!id,
    })),
  })

  const { data: balanceHistory, isLoading: balanceHistoryLoading } = useQuery({
    queryKey: ['accounts', id, 'balance-history', filterFrom, filterTo],
    queryFn: () => accounts.balanceHistory(id!, filterFrom || undefined, filterTo || undefined),
    enabled: !!id,
  })

  const { data: txData, isLoading: txLoading } = useQuery({
    queryKey: ['transactions', { account_id: id, from: filterFrom, to: filterTo, limit: 500, include_opening_balance: true }],
    queryFn: () => transactions.list({
      account_id: id,
      from: filterFrom || undefined,
      to: filterTo || undefined,
      limit: 500,
      include_opening_balance: true,
    }),
    enabled: !!id,
  })

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  const updateMutation = useMutation({
    mutationFn: ({ id: txId, ...data }: Partial<Transaction> & { id: string }) =>
      transactions.update(txId, data),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setDialogOpen(false)
      setEditingTx(null)
      toast.success(t('accounts.updated'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (txId: string) => transactions.delete(txId),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setDialogOpen(false)
      setEditingTx(null)
      toast.success(t('transactions.deleted'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const unlinkTransferMutation = useMutation({
    mutationFn: (pairId: string) => transactions.unlinkTransfer(pairId),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setDialogOpen(false)
      setEditingTx(null)
      toast.success(t('transactions.unlinkTransferSuccess'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  const reopenMutation = useMutation({
    mutationFn: () => accounts.reopen(id!),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      toast.success(t('accounts.accountReopened'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const [ccSettingsOpen, setCcSettingsOpen] = useState(false)
  const ccSettingsMutation = useMutation({
    mutationFn: (data: { credit_limit?: number | null; statement_close_day?: number | null; payment_due_day?: number | null }) =>
      accounts.update(id!, data),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setCcSettingsOpen(false)
      toast.success(t('accounts.updated'))
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const transferMutation = useMutation({
    mutationFn: (data: {
      from_account_id: string
      to_account_id: string
      amount: number
      date: string
      description: string
      notes?: string
      fx_rate?: number
    }) => transactions.createTransfer(data),
    onSuccess: () => {
      invalidateFinancialQueries(queryClient)
      setTransferDialogOpen(false)
      toast.success(t('transactions.transferCreated'))
    },
    onError: (error) => {
      toast.error(extractApiError(error))
    },
  })

  // Whether to use primary currency amounts (for foreign-currency accounts with toggle, or domestic accounts with foreign txs)
  const isCreditCard = account?.type === 'credit_card'
  const isForeignCurrency = account ? account.currency !== userCurrency : false
  const usePrimary = !isForeignCurrency || showPrimary
  const displayCurrency = (isForeignCurrency && !showPrimary) ? (account?.currency || userCurrency) : userCurrency

  // Chart data:
  // - Non-CC: daily running balance from /balance-history
  // - CC: cumulative charges within the current cycle, starting at 0
  //   (answers "how much have I spent this cycle", ignores bill payments/transfers)
  const chartData = useMemo(() => {
    if (isCreditCard) {
      if (!txData?.items || !filterFrom || !filterTo) return []
      const byDay = new Map<string, number>()
      for (const tx of txData.items) {
        if (tx.type !== 'debit') continue
        if (tx.source === 'opening_balance') continue
        if (tx.transfer_pair_id) continue
        const amt = usePrimary && tx.amount_primary != null ? Number(tx.amount_primary) : Number(tx.amount)
        byDay.set(tx.date, (byDay.get(tx.date) ?? 0) + amt)
      }
      const series: { label: string; date: string; balance: number }[] = []
      // Synthetic zero baseline (day before cycle start) so the line always
      // anchors at 0 even when the cycle's first day already has charges.
      const startDate = parseISO(filterFrom + 'T00:00:00')
      const baseline = new Date(startDate.getTime() - 86400000)
      const baselineKey = format(baseline, 'yyyy-MM-dd')
      series.push({ label: formatDateStr(baselineKey, locale), date: baselineKey, balance: 0 })
      const cur = new Date(startDate)
      const end = new Date(filterTo + 'T00:00:00')
      let running = 0
      while (cur <= end) {
        const key = format(cur, 'yyyy-MM-dd')
        running += byDay.get(key) ?? 0
        series.push({ label: formatDateStr(key, locale), date: key, balance: running })
        cur.setDate(cur.getDate() + 1)
      }
      return series
    }
    if (!balanceHistory) return []
    return balanceHistory.map(p => ({
      label: formatDateStr(p.date, locale),
      date: p.date,
      balance: usePrimary ? (p.balance_primary ?? p.balance) : p.balance,
    }))
  }, [isCreditCard, txData, filterFrom, filterTo, balanceHistory, locale, usePrimary])

  // Running balance computation for transaction table
  const txWithRunningBalance = useMemo((): TxWithBalance[] => {
    if (!txData?.items) return []

    if (isCreditCard) {
      // Match the cycle spending chart: cumulative sum of debits (excluding
      // transfers and opening balance), computed oldest → newest, then displayed
      // newest-first. Each row shows the cycle total through that transaction.
      const ascending = [...txData.items].sort(
        (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime(),
      )
      let running = 0
      const withBalance = ascending.map((tx) => {
        if (tx.type === 'debit' && tx.source !== 'opening_balance' && !tx.transfer_pair_id) {
          const amt = usePrimary && tx.amount_primary != null ? Number(tx.amount_primary) : Number(tx.amount)
          running += amt
        }
        return { ...tx, runningBalance: running }
      })
      return withBalance.sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime())
    }

    if (summary === undefined) return []
    const endBalance = usePrimary
      ? (balanceHistory?.length
          ? (balanceHistory[balanceHistory.length - 1].balance_primary ?? balanceHistory[balanceHistory.length - 1].balance)
          : (summary.current_balance_primary ?? summary.current_balance))
      : (balanceHistory?.length
          ? balanceHistory[balanceHistory.length - 1].balance
          : summary.current_balance)
    const sorted = [...txData.items].sort(
      (a, b) => new Date(b.date).getTime() - new Date(a.date).getTime()
    )
    let running = endBalance
    return sorted.map((tx) => {
      const balanceAtPoint = running
      const amt = usePrimary && tx.amount_primary != null ? Number(tx.amount_primary) : Number(tx.amount)
      running -= tx.type === 'credit' ? amt : -amt
      return { ...tx, runningBalance: balanceAtPoint }
    })
  }, [txData, summary, isCreditCard, balanceHistory, usePrimary])

  const resolvedDefaultRange = account?.type === 'credit_card'
    ? defaultCycleForCreditCard(account.statement_close_day, account.payment_due_day, new Date())
    : { start: defaultFrom(), end: defaultTo() }
  const hasFilters = filterFrom !== resolvedDefaultRange.start || filterTo !== resolvedDefaultRange.end

  const isLoading = accountLoading || summaryLoading

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32" />
        <Skeleton className="h-64" />
      </div>
    )
  }

  if (!account) {
    return <p className="text-muted-foreground">{t('accounts.notFound')}</p>
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-6 space-y-4">
        {/* Breadcrumb */}
        <Link
          to="/accounts"
          className="inline-flex items-center text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5 mr-1" />
          {t('accounts.backToAccounts')}
        </Link>

        {/* Title row */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-2xl sm:text-3xl font-semibold text-foreground tracking-tight truncate">
              {getAccountName(account)}
            </h1>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              <span className="text-xs font-medium text-muted-foreground">
                {t(`accounts.type${account.type.split('_').map(s => s[0].toUpperCase() + s.slice(1)).join('')}`, account.type)}
              </span>
              {isCreditCard && account.next_due_date && (() => {
                const d = daysUntil(account.next_due_date)
                if (d > 7) return null
                const cfg = d < 0
                  ? { bg: 'bg-rose-100 dark:bg-rose-500/20', text: 'text-rose-700 dark:text-rose-400', label: t('accounts.overdue') }
                  : d === 0
                    ? { bg: 'bg-rose-100 dark:bg-rose-500/20', text: 'text-rose-700 dark:text-rose-400', label: t('accounts.dueToday') }
                    : d <= 3
                      ? { bg: 'bg-rose-100 dark:bg-rose-500/20', text: 'text-rose-700 dark:text-rose-400', label: t('accounts.dueIn', { count: d }) }
                      : { bg: 'bg-amber-100 dark:bg-amber-500/20', text: 'text-amber-700 dark:text-amber-400', label: t('accounts.dueIn', { count: d }) }
                return (
                  <>
                    <span className="text-muted-foreground text-xs">·</span>
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold ${cfg.bg} ${cfg.text}`}>
                      {cfg.label}
                    </span>
                  </>
                )
              })()}
              {isCreditCard && (!account.statement_close_day || !account.payment_due_day) && (
                <>
                  <span className="text-muted-foreground text-xs">·</span>
                  <button
                    type="button"
                    onClick={() => setCcSettingsOpen(true)}
                    title={t('accounts.cycleMissingHint')}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400 hover:bg-amber-200 dark:hover:bg-amber-500/30 transition-colors cursor-pointer"
                  >
                    <HelpCircle className="h-3 w-3" />
                    {t('accounts.cycleMissing')}
                  </button>
                </>
              )}
            </div>
          </div>
          {!account.is_closed && (
            <Button
              variant="outline"
              size="sm"
              className="shrink-0"
              onClick={() => setTransferDialogOpen(true)}
            >
              <ArrowLeftRight className="h-4 w-4 mr-1" />
              {t('transactions.transfer')}
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          {isCreditCard ? (
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:border-border hover:text-foreground transition-all"
                onClick={() => shiftCycleBy(-1)}
                title={t('accounts.previousCycle')}
              >
                <ChevronLeft size={16} />
              </button>
              <Popover>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-2 min-w-[140px] border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground hover:bg-muted/50 transition-all cursor-pointer capitalize"
                  >
                    {creditCardCycleLabel(filterTo, account?.payment_due_day, i18n.language)}
                  </button>
                </PopoverTrigger>
                <PopoverContent align="center" className="w-auto p-3 space-y-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs">{t('transactions.from')}</Label>
                    <DatePickerInput
                      value={filterFrom}
                      onChange={handleFilterFromChange}
                      placeholder={t('transactions.from')}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">{t('transactions.to')}</Label>
                    <DatePickerInput
                      value={filterTo}
                      onChange={handleFilterToChange}
                      placeholder={t('transactions.to')}
                    />
                  </div>
                </PopoverContent>
              </Popover>
              <button
                type="button"
                className="h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:border-border hover:text-foreground transition-all"
                onClick={() => shiftCycleBy(1)}
                title={t('accounts.nextCycle')}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <label className="text-sm text-muted-foreground hidden md:inline">{t('transactions.from')}</label>
                <DatePickerInput
                  value={filterFrom}
                  onChange={handleFilterFromChange}
                  placeholder={t('transactions.from')}
                />
              </div>
              <div className="flex items-center gap-2">
                <label className="text-sm text-muted-foreground hidden md:inline">{t('transactions.to')}</label>
                <DatePickerInput
                  value={filterTo}
                  onChange={handleFilterToChange}
                  placeholder={t('transactions.to')}
                />
              </div>
            </>
          )}
          {hasFilters && (
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => {
                filterTouched.current = false
                if (account?.type === 'credit_card') {
                  const { start, end } = defaultCycleForCreditCard(
                    account.statement_close_day,
                    account.payment_due_day,
                    new Date(),
                  )
                  setFilterFrom(start)
                  setFilterTo(end)
                } else {
                  setFilterFrom(defaultFrom())
                  setFilterTo(defaultTo())
                }
              }}
            >
              <X className="h-3.5 w-3.5 mr-1" />
              {t('transactions.clearFilters')}
            </Button>
          )}
          {isForeignCurrency && (
            <div className="ml-auto inline-flex rounded-lg border border-border bg-muted p-0.5 text-xs font-medium">
              <button
                onClick={() => setShowPrimary(false)}
                className={`px-3 py-1.5 rounded-md transition-colors ${!showPrimary ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {account.currency}
              </button>
              <button
                onClick={() => setShowPrimary(true)}
                className={`px-3 py-1.5 rounded-md transition-colors ${showPrimary ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
              >
                {userCurrency}
              </button>
            </div>
          )}
        </div>
      </div>

      {account.is_closed && (
        <div className="flex items-center justify-between rounded-lg border border-border bg-muted px-4 py-3 mb-6">
          <span className="text-sm text-muted-foreground">{t('accounts.closedBanner')}</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => reopenMutation.mutate()}
            disabled={reopenMutation.isPending}
          >
            {t('accounts.reopen')}
          </Button>
        </div>
      )}

      {/* Bill timeline (last 6 cycles) — only for CC with cycle metadata */}
      {isCreditCard && timelineCycles.length > 0 && (() => {
        const totals = timelineQueries.map((q, i) => ({
          ...timelineCycles[i],
          total: Number(q.data?.monthly_expenses ?? 0),
          loading: q.isLoading,
        }))
        const max = Math.max(1, ...totals.map(c => c.total))
        return (
          <div className="bg-card rounded-xl border border-border shadow-sm p-3 sm:p-4 mb-6">
            <div className="flex items-end gap-2 sm:gap-3 overflow-x-auto pb-1">
              {totals.map((c, i) => {
                const isCurrent = c.start === filterFrom && c.end === filterTo
                const heightPct = c.total > 0 ? Math.max(8, (c.total / max) * 100) : 4
                const label = creditCardCycleLabel(c.end, account.payment_due_day, i18n.language)
                return (
                  <button
                    key={i}
                    type="button"
                    onClick={() => {
                      filterTouched.current = true
                      setFilterFrom(c.start)
                      setFilterTo(c.end)
                    }}
                    className={`group flex-1 min-w-[60px] flex flex-col items-center gap-1.5 px-1 py-2 rounded-lg transition-colors ${isCurrent ? 'bg-rose-50 dark:bg-rose-500/10' : 'hover:bg-muted/50'}`}
                  >
                    <div className="h-12 w-full flex items-end justify-center">
                      {c.loading ? (
                        <div className="w-6 h-3 rounded-sm bg-muted animate-pulse" />
                      ) : (
                        <div
                          className={`w-6 rounded-sm transition-colors ${isCurrent ? 'bg-rose-500' : c.total > 0 ? 'bg-rose-300 dark:bg-rose-500/40 group-hover:bg-rose-400' : 'bg-muted-foreground/20'}`}
                          style={{ height: `${heightPct}%` }}
                        />
                      )}
                    </div>
                    <p className={`text-[10px] sm:text-xs font-medium capitalize ${isCurrent ? 'text-rose-700 dark:text-rose-400' : 'text-muted-foreground'}`}>
                      {label}
                    </p>
                    <p className={`text-[10px] sm:text-xs font-semibold tabular-nums ${isCurrent ? 'text-foreground' : 'text-muted-foreground'}`}>
                      {c.loading ? '—' : mask(formatCurrency(c.total, account.currency, locale))}
                    </p>
                  </button>
                )
              })}
            </div>
          </div>
        )
      })()}

      {/* Compact stat bar */}
      {isCreditCard ? (() => {
        const billTotal = (showPrimary ? summary?.monthly_expenses_primary : undefined) ?? summary?.monthly_expenses ?? 0
        // "Default cycle" = the bill the user is here to pay (next due). The
        // AGORA tag on Limite disponível only shows when viewing a different cycle.
        const isDefaultCycle =
          filterFrom === resolvedDefaultRange.start && filterTo === resolvedDefaultRange.end
        // Compute the due date for THIS cycle (bill due day after cycle close).
        const cycleDueDate = dueDateForCycle(filterTo, account.payment_due_day)
        const dueIn = cycleDueDate ? daysUntil(cycleDueDate) : null
        // Show the countdown whenever the due date is upcoming (future or today),
        // OR when the default bill is overdue (urgent, needs paying). Hide for
        // past bills the user can't act on.
        const dueSubtitle = (() => {
          if (dueIn == null) return null
          if (dueIn > 0) return t('accounts.dueIn', { count: dueIn })
          if (dueIn === 0) return t('accounts.dueToday')
          if (isDefaultCycle) return t('accounts.overdueDays', { count: Math.abs(dueIn) })
          return null
        })()
        const dueSubtitleClass =
          dueIn != null && dueIn < 0 ? 'text-rose-500'
          : dueIn != null && dueIn <= 3 ? 'text-rose-500'
          : dueIn != null && dueIn <= 7 ? 'text-amber-600'
          : 'text-muted-foreground'
        // Cycle-over-cycle comparison: any time we have a previous cycle to
        // compare against. A current bill of 0 is still meaningful (shows -100%
        // and tells the user "nothing spent yet vs last month").
        const prevTotal = previousCycleSummary?.monthly_expenses ?? 0
        const showComparison = previousCycle && prevTotal > 0
        const deltaPct = showComparison ? ((billTotal - prevTotal) / prevTotal) * 100 : null
        const prevCycleLabel = previousCycle ? creditCardCycleLabel(previousCycle.end, account.payment_due_day, i18n.language) : null
        return (
          <div className="grid grid-cols-3 gap-2 sm:gap-4 mb-6">
            <div className="bg-card rounded-xl border border-border shadow-sm p-3 sm:p-4">
              <p className="text-[10px] sm:text-xs font-medium text-muted-foreground mb-1">
                {t('accounts.cycleBillTotal')}
              </p>
              <p className="text-base sm:text-2xl font-bold tabular-nums text-foreground">
                {mask(formatCurrency(billTotal, displayCurrency, locale))}
              </p>
              {deltaPct != null && prevCycleLabel && (
                <p className={`text-[10px] sm:text-xs font-medium mt-0.5 tabular-nums ${deltaPct > 0 ? 'text-rose-500' : 'text-emerald-600'}`}>
                  {deltaPct > 0 ? '+' : ''}{deltaPct.toFixed(0)}% <span className="text-muted-foreground font-normal">vs {prevCycleLabel}</span>
                </p>
              )}
            </div>
            <div className="bg-card rounded-xl border border-border shadow-sm p-3 sm:p-4">
              <p className="text-[10px] sm:text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1.5">
                {t('accounts.availableCredit')}
                <span className="inline-flex items-center px-1 py-0 rounded text-[9px] font-bold uppercase tracking-wide bg-muted text-muted-foreground">
                  {t('accounts.currentTag')}
                </span>
              </p>
              <p className="text-base sm:text-2xl font-bold tabular-nums text-emerald-600">
                {account.available_credit != null
                  ? mask(formatCurrency(Number(account.available_credit), account.currency, locale))
                  : '—'}
              </p>
            </div>
            <div className="bg-card rounded-xl border border-border shadow-sm p-3 sm:p-4">
              <p className="text-[10px] sm:text-xs font-medium text-muted-foreground mb-1">
                {t('accounts.dueDate')}
              </p>
              <p className="text-base sm:text-2xl font-bold tabular-nums text-foreground">
                {cycleDueDate ? formatFriendlyDate(cycleDueDate, i18n.language) : '—'}
              </p>
              {dueSubtitle && (
                <p className={`text-[10px] sm:text-xs font-medium mt-0.5 ${dueSubtitleClass}`}>
                  {dueSubtitle}
                </p>
              )}
            </div>
          </div>
        )
      })() : (
        <div className="grid grid-cols-3 gap-2 sm:gap-4 mb-6">
          <div className="bg-card rounded-xl border border-border shadow-sm p-3 sm:p-4">
            <p className="text-[10px] sm:text-xs font-medium text-muted-foreground mb-1">
              {t('accounts.currentBalance')}
            </p>
            <p className={`text-base sm:text-2xl font-bold tabular-nums ${(summary?.current_balance ?? 0) < 0 ? 'text-rose-500' : 'text-foreground'}`}>
              {mask(formatCurrency(
                (showPrimary ? summary?.current_balance_primary : undefined) ?? summary?.current_balance ?? 0,
                displayCurrency, locale
              ))}
            </p>
          </div>
          <div className="bg-card rounded-xl border border-border shadow-sm p-3 sm:p-4">
            <p className="text-[10px] sm:text-xs font-medium text-muted-foreground mb-1">
              {t('accounts.income')}
            </p>
            <p className="text-base sm:text-2xl font-bold tabular-nums text-emerald-600">
              {mask(formatCurrency(
                (showPrimary ? summary?.monthly_income_primary : undefined) ?? summary?.monthly_income ?? 0,
                displayCurrency, locale
              ))}
            </p>
          </div>
          <div className="bg-card rounded-xl border border-border shadow-sm p-3 sm:p-4">
            <p className="text-[10px] sm:text-xs font-medium text-muted-foreground mb-1">
              {t('accounts.expenses')}
            </p>
            <p className="text-base sm:text-2xl font-bold tabular-nums text-rose-500">
              {mask(formatCurrency(
                (showPrimary ? summary?.monthly_expenses_primary : undefined) ?? summary?.monthly_expenses ?? 0,
                displayCurrency, locale
              ))}
            </p>
          </div>
        </div>
      )}

      {isCreditCard && (() => {
        const limit = account.credit_limit != null ? Number(account.credit_limit) : null
        // Cycle-bound utilization: how much of the limit was charged in the cycle
        // currently being viewed. For the current cycle this matches the "current
        // open balance" since nothing has been paid yet; for past cycles it shows
        // that month's burn rate against the (current) limit.
        const cycleBillTotal = (showPrimary ? summary?.monthly_expenses_primary : undefined) ?? summary?.monthly_expenses ?? 0
        const utilized = limit != null ? cycleBillTotal : null
        const rawPct = limit != null && limit > 0 && utilized != null ? (utilized / limit) * 100 : null
        const pct = rawPct != null ? Math.min(100, rawPct) : null
        return (
          <div className="bg-card rounded-xl border border-border shadow-sm p-4 sm:p-5 mb-6">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                {account.card_brand && (
                  <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-foreground/5 text-foreground text-[10px] sm:text-xs font-bold tracking-wide uppercase">
                    {account.card_brand}
                  </span>
                )}
                {account.card_level && (
                  <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400 text-[10px] sm:text-xs font-bold tracking-wide uppercase">
                    {account.card_level}
                  </span>
                )}
                {!account.card_brand && !account.card_level && (
                  <p className="text-[10px] sm:text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    {t('accounts.typeCreditCard')}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => setCcSettingsOpen(true)}
                className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                title={t('common.edit')}
              >
                <Pencil size={13} />
              </button>
            </div>
            {limit != null && pct != null && rawPct != null && (
              <>
                <div className="flex items-baseline justify-between mb-2">
                  <p className="text-[10px] sm:text-xs font-medium text-muted-foreground uppercase tracking-wide">
                    {t('accounts.utilization')}
                  </p>
                  <p className={`text-sm font-bold tabular-nums ${rawPct >= 100 ? 'text-rose-500' : 'text-foreground'}`}>{rawPct.toFixed(1)}%</p>
                </div>
                <div className="h-2 bg-muted/60 rounded-full overflow-hidden mb-2">
                  <div
                    className={`h-full rounded-full transition-all ${utilizationColor(rawPct)}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <p className="text-xs text-muted-foreground tabular-nums mb-4">
                  {mask(formatCurrency(utilized ?? 0, account.currency, locale))}
                  {' / '}
                  {mask(formatCurrency(limit, account.currency, locale))}
                </p>
              </>
            )}
            <div className="grid grid-cols-2 gap-3 pt-3 border-t border-border">
              <div>
                <p className="text-[10px] sm:text-xs font-medium text-muted-foreground mb-0.5">
                  {t('accounts.creditLimit')}
                </p>
                <p className="text-sm sm:text-base font-semibold tabular-nums text-foreground">
                  {limit != null ? mask(formatCurrency(limit, account.currency, locale)) : '—'}
                </p>
              </div>
              <div>
                <p className="text-[10px] sm:text-xs font-medium text-muted-foreground mb-0.5">
                  {t('accounts.statementCloseDay')}
                </p>
                <p className="text-sm sm:text-base font-semibold tabular-nums text-foreground">
                  {account.statement_close_day && filterTo
                    ? formatDateStr(format(addDays(parseISO(filterTo), 1), 'yyyy-MM-dd'), locale)
                    : '—'}
                </p>
              </div>
            </div>
          </div>
        )
      })()}

      {/* Balance / Cycle spending chart */}
      {(() => {
        const cycleEmpty = isCreditCard && chartData.length > 0 && chartData[chartData.length - 1].balance === 0
        return (
      <div className="bg-card rounded-xl border border-border shadow-sm mb-6">
        <div className="px-5 pt-5 pb-3">
          <p className="text-base font-bold text-foreground">
            {isCreditCard ? t('accounts.cycleSpending') : t('dashboard.balanceFlow')}
          </p>
        </div>
        <div className="px-1 pb-4 h-[280px]">
          {(isCreditCard ? txLoading : balanceHistoryLoading) ? (
            <Skeleton className="h-full w-full" />
          ) : cycleEmpty ? (
            <div className="h-full w-full flex flex-col items-center justify-center gap-2 text-muted-foreground">
              <Clock className="h-8 w-8 opacity-40" />
              <p className="text-sm">{t('accounts.noChargesYet')}</p>
            </div>
          ) : chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart
                data={chartData}
                margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
              >
                <defs>
                  <linearGradient id="acctBalGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={isCreditCard ? '#F43F5E' : '#10B981'} stopOpacity={0.18} />
                    <stop offset="95%" stopColor={isCreditCard ? '#F43F5E' : '#10B981'} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                  axisLine={false}
                  tickLine={false}
                  interval="preserveStartEnd"
                  minTickGap={40}
                />
                <YAxis
                  tickFormatter={(v) => {
                    if (privacyMode) return ''
                    if (v === 0) return '0'
                    return formatCurrency(v, displayCurrency, locale).replace(/,00$/, '').replace(/\.00$/, '')
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
                  formatter={(value) => [
                    value !== null ? (privacyMode ? MASK : formatCurrency(Number(value), displayCurrency, locale)) : '\u2014',
                    isCreditCard ? t('accounts.cycleSpending') : t('accounts.currentBalance'),
                  ]}
                  labelFormatter={(label) => label}
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
                  dataKey="balance"
                  stroke={isCreditCard ? '#F43F5E' : '#10B981'}
                  strokeWidth={2}
                  fill="url(#acctBalGrad)"
                  dot={false}
                  activeDot={{ r: 3, fill: isCreditCard ? '#F43F5E' : '#10B981' }}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-muted-foreground text-sm text-center py-12">{t('dashboard.noData')}</p>
          )}
        </div>
      </div>
        )
      })()}

      {/* Transaction table */}
      <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <p className="font-semibold text-foreground">{t('transactions.title')}</p>
        </div>
        <div className="p-0">
          {txLoading ? (
            <div className="p-6 space-y-3">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
            </div>
          ) : txWithRunningBalance.length === 0 ? (
            <p className="p-6 text-center text-muted-foreground">{t('accounts.noTransactions')}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="px-3 sm:px-4 py-3 text-left font-medium">{t('transactions.date')}</th>
                    <th className="px-3 sm:px-4 py-3 text-left font-medium">{t('transactions.description')}</th>
                    <th className="px-4 py-3 text-left font-medium hidden md:table-cell">{t('transactions.category')}</th>
                    <th className="px-3 sm:px-4 py-3 text-right font-medium">{t('transactions.amount')}</th>
                    <th className="px-4 py-3 text-right font-medium hidden sm:table-cell">{t('accounts.runningBalance')}</th>
                  </tr>
                </thead>
                <tbody>
                  {txWithRunningBalance.map((tx) => {
                    const isOpening = tx.source === 'opening_balance'
                    const isTransfer = !!tx.transfer_pair_id
                    const isPending = tx.status === 'pending'
                    return (
                      <tr
                        key={tx.id}
                        className={`border-b last:border-0 transition-colors ${isOpening ? 'bg-muted/60' : isPending ? 'opacity-60' : 'hover:bg-muted cursor-pointer'}`}
                        onClick={() => {
                          if (!isOpening) {
                            setEditingTx(tx)
                            setDialogOpen(true)
                          }
                        }}
                      >
                        <td className="px-3 sm:px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">
                          {formatDateStr(tx.date, locale)}
                        </td>
                        <td className="px-3 sm:px-4 py-3">
                          <div>
                            <span className="font-semibold text-foreground text-sm">{tx.description}</span>
                            {isOpening && (
                              <span className="ml-2 text-xs text-muted-foreground font-normal border border-border rounded px-1.5 py-0.5">
                                {t('accounts.openingBalance')}
                              </span>
                            )}
                            {isTransfer && (
                              <span className="ml-2 inline-flex items-center gap-1 text-xs text-blue-600 font-normal bg-blue-50 border border-blue-200 rounded px-1.5 py-0.5">
                                <ArrowLeftRight className="h-3 w-3" />
                                {t('transactions.transfer')}
                                <span title={t('transactions.transferTooltip')}><HelpCircle className="h-3 w-3 text-blue-400" /></span>
                              </span>
                            )}
                            {isPending && (
                              <span className="ml-2 inline-flex items-center gap-1 text-xs text-amber-600 font-normal bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
                                <Clock className="h-3 w-3" />
                                {t('transactions.pending')}
                              </span>
                            )}
                            {tx.installment_number != null && tx.total_installments != null && (
                              <span
                                className="ml-2 inline-flex items-center text-[10px] font-bold tabular-nums text-amber-700 dark:text-amber-400 bg-amber-100 dark:bg-amber-500/20 border border-amber-200 dark:border-amber-500/30 px-1.5 py-0.5 rounded-full"
                                title={tx.installment_total_amount != null
                                  ? t('transactions.installmentTooltip', { count: tx.total_installments, total: tx.installment_total_amount })
                                  : undefined}
                              >
                                {tx.installment_number}/{tx.total_installments}
                              </span>
                            )}
                            {(tx.attachment_count ?? 0) > 0 && (
                              <Paperclip size={12} className="ml-2 inline text-muted-foreground" />
                            )}
                          </div>
                          {(tx.payee_name || tx.payee) && (tx.payee_name || tx.payee) !== tx.description && (
                            <p className="text-xs text-muted-foreground mt-0.5">{tx.payee_name || tx.payee}</p>
                          )}
                        </td>
                        <td className="px-4 py-3 hidden md:table-cell">
                          {tx.category ? (
                            <span className="flex items-center gap-1.5">
                              <CategoryIcon icon={tx.category.icon} color={tx.category.color} size="sm" />
                              <span className="text-sm text-muted-foreground">{tx.category.name}</span>
                            </span>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </td>
                        <td className={`px-3 sm:px-4 py-3 text-right text-xs sm:text-sm font-semibold tabular-nums ${tx.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
                          {mask(`${tx.type === 'credit' ? '+' : '-'}${formatCurrency(Math.abs(Number(tx.amount)), tx.currency, locale)}`)}
                          {tx.currency !== userCurrency && tx.amount_primary != null && (
                            <span className="block text-[10px] text-muted-foreground tabular-nums">
                              {mask(formatCurrency(Math.abs(tx.amount_primary), userCurrency, locale))}
                            </span>
                          )}
                        </td>
                        <td className={`px-4 py-3 text-right tabular-nums text-sm hidden sm:table-cell ${(account.type === 'credit_card' ? tx.runningBalance > 0 : tx.runningBalance < 0) ? 'text-rose-500' : 'text-muted-foreground'}`}>
                          {mask(formatCurrency(tx.runningBalance, displayCurrency, locale))}
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

      <TransactionDialog
        open={dialogOpen}
        onClose={() => { setDialogOpen(false); setEditingTx(null) }}
        transaction={editingTx}
        categories={categoriesList ?? []}
        accounts={accountsList ?? []}
        onSave={(data) => {
          if (editingTx) {
            updateMutation.mutate({ id: editingTx.id, ...data })
          }
        }}
        onDelete={editingTx ? () => deleteMutation.mutate(editingTx.id) : undefined}
        onUnlinkTransfer={(pairId) => unlinkTransferMutation.mutate(pairId)}
        loading={updateMutation.isPending || deleteMutation.isPending || unlinkTransferMutation.isPending}
        error={updateMutation.error ? extractApiError(updateMutation.error) : null}
        isSynced={editingTx?.source === 'sync'}
      />

      <TransferDialog
        open={transferDialogOpen}
        onClose={() => setTransferDialogOpen(false)}
        accounts={accountsList ?? []}
        onSave={(data) => transferMutation.mutate(data)}
        loading={transferMutation.isPending}
        defaultFromAccountId={id}
      />

      {account && (
        <CreditCardSettingsDialog
          open={ccSettingsOpen}
          onClose={() => setCcSettingsOpen(false)}
          account={account}
          onSave={(data) => ccSettingsMutation.mutate(data)}
          loading={ccSettingsMutation.isPending}
        />
      )}
    </div>
  )
}

function CreditCardSettingsDialog({
  open,
  onClose,
  account,
  onSave,
  loading,
}: {
  open: boolean
  onClose: () => void
  account: { credit_limit: number | null; statement_close_day: number | null; payment_due_day: number | null }
  onSave: (data: { credit_limit: number | null; statement_close_day: number | null; payment_due_day: number | null }) => void
  loading: boolean
}) {
  const { t } = useTranslation()
  const [creditLimit, setCreditLimit] = useState('')
  const [closeDay, setCloseDay] = useState('')
  const [dueDay, setDueDay] = useState('')

  useEffect(() => {
    if (!open) return
    setCreditLimit(account.credit_limit != null ? String(account.credit_limit) : '')
    setCloseDay(account.statement_close_day != null ? String(account.statement_close_day) : '')
    setDueDay(account.payment_due_day != null ? String(account.payment_due_day) : '')
  }, [open, account.credit_limit, account.statement_close_day, account.payment_due_day])

  const parseDay = (v: string): number | null => {
    const n = parseInt(v, 10)
    return Number.isFinite(n) && n >= 1 && n <= 31 ? n : null
  }

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('accounts.typeCreditCard')}</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            onSave({
              credit_limit: creditLimit !== '' ? parseFloat(creditLimit) : null,
              statement_close_day: parseDay(closeDay),
              payment_due_day: parseDay(dueDay),
            })
          }}
          className="space-y-4"
        >
          {(!account.statement_close_day || !account.payment_due_day) && (
            <p className="text-xs text-muted-foreground leading-relaxed">
              {t('accounts.ccSettingsHint')}
            </p>
          )}
          <div className="space-y-2">
            <Label>{t('accounts.creditLimit')}</Label>
            <Input
              type="number"
              step="0.01"
              min="0"
              value={creditLimit}
              onChange={(e) => setCreditLimit(e.target.value)}
              placeholder="0.00"
            />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>{t('accounts.statementCloseDay')}</Label>
              <Input
                type="number"
                min="1"
                max="31"
                value={closeDay}
                onChange={(e) => setCloseDay(e.target.value)}
                placeholder={t('accounts.dayOfMonthHint')}
              />
            </div>
            <div className="space-y-2">
              <Label>{t('accounts.paymentDueDay')}</Label>
              <Input
                type="number"
                min="1"
                max="31"
                value={dueDay}
                onChange={(e) => setDueDay(e.target.value)}
                placeholder={t('accounts.dayOfMonthHint')}
              />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={loading}>
              {loading ? t('common.loading') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
