import { useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Cell, Pie, PieChart, ResponsiveContainer } from 'recharts'
import { ArrowRight, RefreshCw, SendHorizontal, Sparkles } from 'lucide-react'

import { accounts as accountsApi, budgets, dashboard } from '@/lib/api'
import { sumAccountBalances, getAccountLabel } from '@/lib/account-utils'
import { formatCurrency } from '@/lib/format'
import { monthLastDay, shiftMonth } from '@/lib/month-utils'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAssistant } from '@/contexts/assistant-context'
import { AccountIcon } from '@/components/account-icon'
import { CategoryIcon } from '@/components/category-icon'
import { Skeleton } from '@/components/ui/skeleton'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { DashboardSummary } from '@/types'

/**
 * What the dashboard is looking at, for widgets that fetch their own data.
 * They read through the same query keys as the dashboard, so what's already
 * loaded is shared, and they only load anything while they're on it.
 */
export interface DashboardScope {
  /** The month on screen, `YYYY-MM`. */
  month: string
  activeAccountIds: string[] | null
  activeWalletIds: string[] | null
  /** The collection holds only wallets: nothing account-based to show. */
  noAccounts: boolean
  /** The currency amounts are converted to. */
  currency: string
}

function WidgetCard({ title, action, children, className }: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn('@container flex flex-col rounded-xl border border-border bg-card shadow-sm', className)}>
      <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
        <p className="truncate text-sm font-semibold text-foreground">{title}</p>
        {action}
      </div>
      <div className="flex flex-1 flex-col p-5">{children}</div>
    </div>
  )
}

function CardLink({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className="shrink-0 text-xs font-medium text-primary hover:underline">
      {label} &rarr;
    </Link>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="flex flex-1 items-center justify-center py-6 text-center text-sm text-muted-foreground">{children}</p>
}

/** A load that failed: said so, with a retry, never shown as an empty result. */
function Failed({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 py-6 text-center">
      <p className="text-sm text-muted-foreground">{t('widgets.loadFailed')}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw size={14} className="mr-1" />
        {t('common.retry')}
      </Button>
    </div>
  )
}

function useSummary(scope: DashboardScope, month = scope.month) {
  const accountIds = scope.activeAccountIds ?? undefined
  const walletIds = scope.activeWalletIds ?? undefined
  return useQuery<DashboardSummary>({
    queryKey: ['dashboard', 'summary', month, scope.activeAccountIds, scope.activeWalletIds],
    queryFn: () => dashboard.summary(`${month}-01`, undefined, accountIds, walletIds),
  })
}

const expensesOf = (summary?: DashboardSummary) => Number(summary?.monthly_expenses_primary ?? summary?.monthly_expenses ?? 0)
const incomeOf = (summary?: DashboardSummary) => Number(summary?.monthly_income_primary ?? summary?.monthly_income ?? 0)

function todayMonth() {
  const today = new Date()
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
}

/** This month's spending against the sum of its budgets, and how much a day is left. */
export function SpendingPaceWidget({ scope }: { scope: DashboardScope }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const { data: summary, isLoading, isError, refetch } = useSummary(scope)
  const { data: comparison, isLoading: budgetsLoading, isError: budgetsError, refetch: refetchBudgets } = useQuery({
    queryKey: ['budgets', 'comparison', scope.month],
    queryFn: () => budgets.comparison(`${scope.month}-01`),
  })
  const money = (value: number) => mask(formatCurrency(value, scope.currency, locale))
  const spent = expensesOf(summary)
  const budget = (comparison ?? []).reduce((sum, row) => sum + Number(row.budget_amount ?? 0), 0)

  const lastDay = monthLastDay(scope.month)
  const isCurrent = scope.month === todayMonth()
  const today = isCurrent ? new Date().getDate() : scope.month < todayMonth() ? lastDay : 0
  const used = budget > 0 ? (spent / budget) * 100 : 0
  // Where spending would be today if it went evenly through the month.
  const pace = (today / lastDay) * 100
  const left = budget - spent
  const daysLeft = lastDay - today + 1

  return (
    <WidgetCard title={t('widgets.spendingPace.title')} action={<CardLink to="/budgets" label={t('widgets.budgets')} />}>
      {isLoading || budgetsLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : isError || budgetsError ? (
        <Failed onRetry={() => { void refetch(); void refetchBudgets() }} />
      ) : budget <= 0 ? (
        <div className="flex flex-1 flex-col justify-between gap-4">
          <p className="text-2xl font-bold tabular-nums">{money(spent)}</p>
          <p className="text-xs text-muted-foreground">{t('widgets.spendingPace.noBudget')}</p>
        </div>
      ) : (
        <div className="flex flex-1 flex-col justify-between gap-4">
          <p className="flex flex-wrap items-baseline gap-x-1.5">
            <span className="text-2xl font-bold tabular-nums">{money(spent)}</span>
            <span className="text-sm text-muted-foreground tabular-nums">/ {money(budget)}</span>
          </p>
          <div>
            <div className="relative h-2 overflow-hidden rounded-full bg-muted">
              <div
                className={cn('h-full rounded-full', used > 100 ? 'bg-rose-500' : used > pace ? 'bg-amber-400' : 'bg-emerald-500')}
                style={{ width: `${Math.min(used, 100)}%` }}
              />
              {isCurrent && (
                <span aria-hidden className="absolute top-0 h-full w-0.5 bg-foreground/50" style={{ left: `${pace}%` }} />
              )}
            </div>
            <p className="mt-1.5 text-[11px] text-muted-foreground tabular-nums">
              {t('widgets.spendingPace.used', { percent: Math.round(used) })}
              {isCurrent && ` · ${t('widgets.spendingPace.paceMark')}`}
            </p>
          </div>
          <p className="text-xs text-muted-foreground">
            {left < 0
              ? t('widgets.spendingPace.over', { amount: money(-left) })
              : isCurrent
                ? t('widgets.spendingPace.perDay', { amount: money(left / daysLeft) })
                : t('widgets.spendingPace.left', { amount: money(left) })}
          </p>
        </div>
      )}
    </WidgetCard>
  )
}

/** Where the month's money went, as a ring and the categories that took the most. */
export function SpendingDistributionWidget({ scope }: { scope: DashboardScope }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const accountIds = scope.activeAccountIds ?? undefined
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['dashboard', 'spending', scope.month, scope.activeAccountIds],
    queryFn: () => dashboard.spendingByCategory(`${scope.month}-01`, accountIds),
    enabled: !scope.noAccounts,
  })
  // The all-in amount, settled plus pending and recurring projections, as the
  // spending-by-category widget shows it.
  const rows = useMemo(
    () =>
      (data ?? [])
        .map((row) => ({ ...row, total: Number(row.projected_total ?? row.total) }))
        .filter((row) => row.total > 0)
        .sort((a, b) => b.total - a.total),
    [data],
  )
  const total = rows.reduce((sum, row) => sum + row.total, 0)
  const top = rows.slice(0, 5)
  const rest = rows.slice(5).reduce((sum, row) => sum + row.total, 0)
  const slices = [
    ...top.map((row) => ({ key: row.category_id ?? row.category_name, value: row.total, color: row.category_color || 'var(--chart-muted)' })),
    ...(rest > 0 ? [{ key: 'other', value: rest, color: 'var(--chart-muted)' }] : []),
  ]
  const money = (value: number) => mask(formatCurrency(value, scope.currency, locale))

  return (
    <WidgetCard title={t('widgets.distribution.title')}>
      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : isError ? (
        <Failed onRetry={() => void refetch()} />
      ) : total <= 0 ? (
        <Empty>{t('dashboard.noData')}</Empty>
      ) : (
        <div className="flex flex-1 flex-col items-center gap-5 @lg:flex-row">
          <div className="relative size-40 shrink-0">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={slices} dataKey="value" nameKey="key" innerRadius="72%" outerRadius="100%" stroke="none" paddingAngle={1.5} isAnimationActive={false}>
                  {slices.map((slice) => (
                    <Cell key={slice.key} fill={slice.color} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
              <span className="text-[11px] text-muted-foreground">{t('widgets.distribution.total')}</span>
              <span className="text-base font-bold tabular-nums">{money(total)}</span>
            </div>
          </div>
          <ul className="w-full min-w-0 flex-1 divide-y divide-border">
            {top.map((row) => (
              <li key={row.category_id ?? row.category_name} className="flex items-center gap-3 py-2 first:pt-0 last:pb-0">
                <CategoryIcon icon={row.category_icon} color={row.category_color} size="sm" />
                <span className="min-w-0 flex-1 truncate text-sm">{row.category_name}</span>
                <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-muted-foreground">
                  {Math.round((row.total / total) * 100)}%
                </span>
                <span className="w-24 text-right text-sm font-semibold tabular-nums">{money(row.total)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </WidgetCard>
  )
}

/** The month so far: what came in, what went out, and the result against last month. */
export function MonthResultWidget({ scope }: { scope: DashboardScope }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const { data: summary, isLoading, isError, refetch } = useSummary(scope)
  const { data: previous } = useSummary(scope, shiftMonth(scope.month, -1))
  const income = incomeOf(summary)
  const expenses = expensesOf(summary)
  const result = income - expenses
  const before = previous ? incomeOf(previous) - expensesOf(previous) : null
  const money = (value: number) => mask(formatCurrency(value, scope.currency, locale))

  return (
    <WidgetCard title={t('widgets.monthResult.title')}>
      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : isError ? (
        <Failed onRetry={() => void refetch()} />
      ) : (
        <div className="flex flex-1 flex-col justify-between gap-4">
          <div>
            <p className={cn('text-2xl font-bold tabular-nums', result < 0 ? 'text-rose-500' : 'text-foreground')}>
              {result > 0 ? '+' : ''}
              {money(result)}
            </p>
            {before !== null && (
              <p className="mt-1 text-xs text-muted-foreground tabular-nums">{t('widgets.monthResult.previous', { amount: money(before) })}</p>
            )}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-xs text-muted-foreground">{t('dashboard.monthlyIncome')}</p>
              <p className="text-sm font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">+{money(income)}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">{t('dashboard.monthlyExpenses')}</p>
              <p className="text-sm font-semibold tabular-nums text-rose-500">-{money(expenses)}</p>
            </div>
          </div>
        </div>
      )}
    </WidgetCard>
  )
}

const UPCOMING_DAYS = 30

/** Recurring transactions due in the next 30 days. */
export function UpcomingWidget({ scope }: { scope: DashboardScope }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const today = new Date()
  const iso = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  const from = iso(today)
  const to = iso(new Date(today.getFullYear(), today.getMonth(), today.getDate() + UPCOMING_DAYS))
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['dashboard', 'projected-transactions', 'upcoming', from],
    queryFn: () => dashboard.projectedTransactions({ from, to }),
  })
  // Within the active collection: a recurring transaction counts when its account is in it.
  const scoped = scope.activeAccountIds
    ? (data ?? []).filter((row) => row.account_id !== null && scope.activeAccountIds!.includes(row.account_id))
    : (data ?? [])
  const rows = [...scoped].sort((a, b) => a.date.localeCompare(b.date)).slice(0, 5)

  return (
    <WidgetCard title={t('widgets.upcoming.title')} action={<CardLink to="/recurring" label={t('widgets.viewAll')} />}>
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : isError ? (
        <Failed onRetry={() => void refetch()} />
      ) : rows.length === 0 ? (
        <Empty>{t('widgets.upcoming.none', { days: UPCOMING_DAYS })}</Empty>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((row) => {
            const amount = Number(row.amount_primary ?? row.amount)
            const currency = row.amount_primary != null ? scope.currency : row.currency
            return (
              <li key={`${row.recurring_id}-${row.date}`} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                <CategoryIcon icon={row.category_icon} color={row.category_color} size="sm" />
                <span className="min-w-0 flex-1 truncate text-sm">{row.description}</span>
                <span className={cn('text-sm font-semibold tabular-nums', row.type === 'credit' ? 'text-emerald-600 dark:text-emerald-400' : 'text-foreground')}>
                  {row.type === 'credit' ? '+' : '-'}
                  {mask(formatCurrency(Math.abs(amount), currency, locale))}
                </span>
                <span className="w-14 text-right text-xs tabular-nums text-muted-foreground">
                  {new Date(`${row.date}T00:00:00`).toLocaleDateString(dateLocale, { day: '2-digit', month: 'short' })}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </WidgetCard>
  )
}

/** What the open credit cards owe, and the balance left once they're paid. */
export function CreditCardsWidget({ scope }: { scope: DashboardScope }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { data: list, isLoading, isError, refetch } = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list() })
  const scoped = (list ?? []).filter((a) => !a.is_closed && (scope.activeAccountIds ? scope.activeAccountIds.includes(a.id) : true))
  const cards = scoped.filter((a) => a.type === 'credit_card')
  const owed = Math.max(0, -sumAccountBalances(cards))
  const available = scoped
    .filter((a) => a.type === 'checking' || a.type === 'savings')
    .reduce((sum, a) => sum + Number(a.balance_primary ?? a.current_balance), 0)
  const money = (value: number, currency = scope.currency) => mask(formatCurrency(value, currency, locale))
  // Cards on one shared credit line report the line's whole balance each: list
  // the line once, under all its cards' names, so the rows add up to the total.
  // The line is due on the earliest of its cards' due dates.
  const lines = new Map<string, { card: (typeof cards)[number]; label: string; owes: number; due: string | null }>()
  for (const card of cards) {
    const key = card.shared_balance_group ?? card.id
    const existing = lines.get(key)
    if (existing) {
      existing.label = `${existing.label} · ${getAccountLabel(card)}`
      if (card.next_due_date && (!existing.due || card.next_due_date < existing.due)) existing.due = card.next_due_date
      continue
    }
    lines.set(key, {
      card,
      label: getAccountLabel(card),
      owes: Math.max(0, -Number(card.balance_primary ?? card.current_balance)),
      due: card.next_due_date,
    })
  }
  const shown = [...lines.values()]
    .filter((row) => row.owes > 0)
    .sort((a, b) => b.owes - a.owes)
    .slice(0, 4)

  return (
    <WidgetCard title={t('widgets.cards.title')} action={<CardLink to="/accounts" label={t('widgets.viewAll')} />}>
      {isLoading ? (
        <Skeleton className="h-28 w-full" />
      ) : isError ? (
        <Failed onRetry={() => void refetch()} />
      ) : cards.length === 0 ? (
        <Empty>{t('widgets.cards.none')}</Empty>
      ) : (
        <div className="flex flex-1 flex-col justify-between gap-4">
          <div>
            <p className="text-2xl font-bold tabular-nums">{money(owed)}</p>
            <p className="mt-1 text-xs text-muted-foreground tabular-nums">
              {t('widgets.cards.afterPaying', { amount: money(available - owed) })}
            </p>
          </div>
          {shown.length > 0 && (
            <ul className="space-y-2">
              {shown.map(({ card, label, owes, due }) => (
                <li key={card.id} className="flex items-center gap-2.5">
                  <AccountIcon account={card} size="xs" className="h-5 w-5 rounded-md" />
                  <span className="min-w-0 flex-1 truncate text-sm" title={label}>{label}</span>
                  <span className="text-sm font-semibold tabular-nums">{money(owes)}</span>
                  {due && (
                    <span className="w-14 text-right text-xs tabular-nums text-muted-foreground">
                      {new Date(`${due}T00:00:00`).toLocaleDateString(dateLocale, { day: '2-digit', month: 'short' })}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </WidgetCard>
  )
}

/** A question for the agent, straight from the dashboard; the answer opens in the chat. */
export function AssistantWidget() {
  const { t } = useTranslation()
  const { available, ask } = useAssistant()
  const [message, setMessage] = useState('')
  const suggestions = [t('widgets.assistant.suggestion1'), t('widgets.assistant.suggestion2'), t('widgets.assistant.suggestion3')]

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const text = message.trim()
    if (!text) return
    ask(text)
    setMessage('')
  }

  return (
    <WidgetCard title={t('widgets.assistant.title')}>
      {!available ? (
        <Empty>{t('widgets.assistant.unavailable')}</Empty>
      ) : (
        <div className="flex flex-1 flex-col justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Sparkles size={18} />
            </span>
            <p className="text-sm text-muted-foreground">{t('widgets.assistant.hint')}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {suggestions.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => ask(suggestion)}
                className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-foreground/25 hover:text-foreground"
              >
                {suggestion}
                <ArrowRight size={12} />
              </button>
            ))}
          </div>
          <form onSubmit={submit} className="flex items-center gap-2 rounded-lg border border-border bg-background p-1 pl-3 focus-within:ring-2 focus-within:ring-ring/30">
            <input
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder={t('widgets.assistant.placeholder')}
              aria-label={t('widgets.assistant.placeholder')}
              className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            <Button type="submit" size="sm" disabled={!message.trim()} aria-label={t('widgets.assistant.send')}>
              <SendHorizontal size={14} />
            </Button>
          </form>
        </div>
      )}
    </WidgetCard>
  )
}
