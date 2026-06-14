import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { useQuery } from '@tanstack/react-query'
import {
  AreaChart,
  Area,
  Bar,
  ComposedChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
} from 'recharts'
import { HelpCircle } from 'lucide-react'
import { reports } from '@/lib/api'
import { Skeleton } from '@/components/ui/skeleton'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { PageHeader } from '@/components/page-header'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useCollectionFilter } from '@/contexts/collection-filter-context'
import type { ReportResponse, CategoryTrendItem } from '@/types'

// A small qualitative palette of well-separated hues for the composition
// detail ring. Capped to a handful of slices, distinct colours make each
// holding easy to match against its legend entry (which a same-hue ramp
// across 15+ near-identical slices never could).
const SLICE_COLORS = [
  '#6366F1', // indigo
  '#F59E0B', // amber
  '#10B981', // emerald
  '#EC4899', // pink
  '#0EA5E9', // sky
  '#8B5CF6', // violet
  '#F97316', // orange
  '#14B8A6', // teal
  '#84CC16', // lime
  '#D946EF', // fuchsia
]
const OTHER_SLICE_COLOR = '#9CA3AF'

function formatCurrency(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value)
}

function formatCompact(value: number, currency = 'USD', locale = 'en-US') {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}



type RangeOption = { key: string; months: number; period?: 'ytd' }

const HISTORICAL_RANGE_OPTIONS: readonly RangeOption[] = [
  { key: '6m', months: 6 },
  { key: 'ytd', months: 12, period: 'ytd' },
  { key: '1y', months: 12 },
  { key: '2y', months: 24 },
]

const FORWARD_RANGE_OPTIONS: readonly RangeOption[] = [
  { key: '3m', months: 3 },
  { key: '6m', months: 6 },
  { key: '12m', months: 12 },
]

const HISTORICAL_INTERVAL_OPTIONS = [
  { key: 'daily', value: 'daily' },
  { key: 'weekly', value: 'weekly' },
  { key: 'monthly', value: 'monthly' },
  { key: 'yearly', value: 'yearly' },
] as const

const CASH_FLOW_INTERVAL_OPTIONS = [
  { key: 'daily', value: 'daily' },
  { key: 'weekly', value: 'weekly' },
  { key: 'monthly', value: 'monthly' },
] as const

const INTERVAL_LABELS: Record<string, string> = {
  daily: 'intervalDaily',
  weekly: 'intervalWeekly',
  monthly: 'intervalMonthly',
  yearly: 'intervalYearly',
}

const RANGE_LABELS: Record<string, string> = {
  '3m': 'range3m',
  '6m': 'range6m',
  '1y': 'range1y',
  ytd: 'rangeYtd',
  '12m': 'range12m',
  '2y': 'range2y',
}

interface ReportTab {
  key: string
  labelKey: string
  enabled: boolean
}

const REPORT_TABS: ReportTab[] = [
  { key: 'net_worth', labelKey: 'reports.netWorth', enabled: true },
  { key: 'income_expenses', labelKey: 'reports.incomeExpenses', enabled: true },
  { key: 'cash_flow', labelKey: 'reports.cashFlow', enabled: true },
]

export default function ReportsPage() {
  const { t } = useTranslation()
  const { mask, privacyMode, MASK } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()

  const [rangeKey, setRangeKey] = useState('1y')
  const [interval, setInterval] = useState('monthly')
  const [activeTab, setActiveTab] = useState('net_worth')
  const [compositionView, setCompositionView] = useState<string>('netWorth')
  const [sparklineView, setSparklineView] = useState<'byExpenses' | 'byIncome'>('byExpenses')
  const [sparklinePage, setSparklinePage] = useState(0)
  const [cashFlowBaseline, setCashFlowBaseline] = useState(false)
  // Active Collection filter (issue #105): scope all report tabs to its
  // accounts; net worth also includes the collection's wallets' assets.
  const { activeAccountIds, activeWalletIds } = useCollectionFilter()
  const acctIds = activeAccountIds ?? undefined
  const walletIds = activeWalletIds ?? undefined
  // Wallet-only collection (active, zero accounts): the account-based reports
  // (income/expenses, cash flow) have no data — only net worth (which includes
  // the wallets' assets) is meaningful.
  const noAccounts = activeAccountIds !== null && activeAccountIds.length === 0

  const currentTab = REPORT_TABS.find((tab) => tab.key === activeTab) ?? REPORT_TABS[0]

  const isCashFlow = activeTab === 'cash_flow'
  const rangeOptions = isCashFlow ? FORWARD_RANGE_OPTIONS : HISTORICAL_RANGE_OPTIONS
  const intervalOptions = isCashFlow ? CASH_FLOW_INTERVAL_OPTIONS : HISTORICAL_INTERVAL_OPTIONS
  const selectedRange = rangeOptions.find((r) => r.key === rangeKey) ?? rangeOptions[0]
  const months = selectedRange.months
  const period = selectedRange.period

  const handleSelectTab = (key: string) => {
    setActiveTab(key)
    setCompositionView(key === 'net_worth' ? 'netWorth' : 'net')
    setSparklinePage(0)
    // Clamp months/interval to options supported by the new tab
    const nextRanges = key === 'cash_flow' ? FORWARD_RANGE_OPTIONS : HISTORICAL_RANGE_OPTIONS
    if (!nextRanges.some((r) => r.key === rangeKey)) {
      setRangeKey(key === 'cash_flow' ? '6m' : '1y')
    }
    const nextIntervals = key === 'cash_flow' ? CASH_FLOW_INTERVAL_OPTIONS : HISTORICAL_INTERVAL_OPTIONS
    if (!nextIntervals.some((i) => i.value === interval)) {
      setInterval(key === 'cash_flow' ? 'daily' : 'monthly')
    }
  }

  const { data, isLoading } = useQuery<ReportResponse>({
    queryKey: ['reports', activeTab, rangeKey, months, period ?? null, interval, isCashFlow ? cashFlowBaseline : false, activeAccountIds, activeWalletIds],
    queryFn: () =>
      isCashFlow
        ? reports.cashFlow(months, interval, cashFlowBaseline, acctIds)
        : activeTab === 'income_expenses'
          ? reports.incomeExpenses(months, interval, acctIds, period)
          : reports.netWorth(months, interval, acctIds, walletIds, period),
    enabled: currentTab.enabled && !(noAccounts && activeTab !== 'net_worth'),
  })

  const summary = data?.summary
  const trend = data?.trend ?? []
  const meta = data?.meta

  // For cash flow we split the line at `forecast_start_date` so the past
  // section renders solid and the forward projection renders dashed.
  // The boundary point is duplicated in both series so the line visually
  // connects without a gap.
  const forecastStart = meta?.forecast_start_date ?? null
  const NEGATIVE_SERIES = new Set(['liabilities'])

  const chartData = trend.map((dp) => {
    const isPast = forecastStart ? dp.date < forecastStart : false
    const isBoundary = forecastStart ? dp.date === forecastStart : false
    const breakdowns = meta?.type === 'net_worth'
      ? Object.fromEntries(Object.entries(dp.breakdowns).map(([k, v]) => [k, NEGATIVE_SERIES.has(k) ? -v : v]))
      : dp.breakdowns
    return {
      date: dp.date,
      value: dp.value,
      change: dp.change ?? null,
      valuePast: isPast || isBoundary ? dp.value : null,
      valueForecast: !isPast ? dp.value : null,
      ...breakdowns,
    } as Record<string, string | number | null>
  })

  const allBreakdowns = summary?.breakdowns ?? []
  const breakdownData = allBreakdowns.filter((b) => b.value > 0)

  const colorMap: Record<string, string> = {}
  for (const b of allBreakdowns) {
    colorMap[b.key] = b.color
  }

  const changePrefix = (summary?.change_amount ?? 0) >= 0 ? '+' : ''
  const changeColor = (summary?.change_amount ?? 0) >= 0 ? 'text-emerald-600' : 'text-rose-500'

  const tooltipStyle = {
    background: 'var(--card)',
    color: 'var(--foreground)',
    border: '1px solid var(--border)',
    borderRadius: '0.75rem',
    boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
    fontSize: '12px',
  }

  const composition = data?.composition ?? []

  // Composition toggle options per report type
  const compositionOptions = activeTab === 'net_worth'
    ? ['netWorth', 'assetsAndAccounts', 'liabilities'] as const
    : activeTab === 'income_expenses' || activeTab === 'cash_flow'
      ? ['net', 'byIncome', 'byExpenses'] as const
      : ['summary', 'detailed'] as const

  // Which breakdown groups are visible in each toggle state. null = show all.
  const activeCompositionGroups: Set<string> | null = (() => {
    if (compositionView === 'assetsAndAccounts') return new Set(['accounts', 'assets'])
    if (compositionView === 'liabilities') return new Set(['liabilities'])
    if (compositionView === 'byIncome') return new Set(['income'])
    if (compositionView === 'byExpenses') return new Set(['expenses'])
    return null
  })()


  // Normalize a breakdown key to its composition group. Cash flow exposes its
  // income/expense breakdowns under projected* keys, but composition items are
  // tagged with the plain group, so the two must be reconciled to line up.
  const groupOf = (key: string) =>
    key === 'projectedIncome' ? 'income'
      : key === 'projectedExpenses' ? 'expenses'
        : key

  // Inner ring — summary view (high-level breakdown), filtered by toggle state for net_worth
  const innerDonutData = (() => {
    const excludedKeys = new Set(['netIncome', 'startingBalance', 'endingBalance'])
    return breakdownData
      .filter((b) => !excludedKeys.has(b.key) && (!activeCompositionGroups || activeCompositionGroups.has(groupOf(b.key))))
      .map((b) => ({
        name: t(`reports.${b.key}`, { defaultValue: b.label }),
        value: b.value,
        color: b.color,
      }))
  })()

  // Full detail — every holding in the active group(s), largest first, labelled
  // and coloured. The donut draws only the top slice of this; the legend popover
  // lists all of it. Net worth items get a distinct palette (the long tail falls
  // back to the neutral colour); income/expense items keep the user's category colour.
  const compositionDetail = (() => {
    if (composition.length === 0) return []

    const excludedKeys = new Set(['netIncome', 'startingBalance', 'endingBalance'])
    const activeGroups = new Set(
      breakdownData
        .filter((b) => !excludedKeys.has(b.key) && (!activeCompositionGroups || activeCompositionGroups.has(groupOf(b.key))))
        .map((b) => groupOf(b.key))
    )

    const itemLabel = (c: { label: string; key: string; group: string }) => {
      if (c.key === 'uncategorized') {
        // Uncategorized income and uncategorized expenses are distinct buckets
        // that share a label — qualify them by group so they don't look duplicated.
        const g = c.group === 'income' ? t('reports.income')
          : c.group === 'expenses' ? t('reports.expenses')
          : null
        return g ? `${t('reports.uncategorized')} · ${g}` : t('reports.uncategorized')
      }
      if (c.key === 'baseline') return t('reports.baseline')
      return c.label
    }

    return composition
      .filter((c) => activeGroups.has(c.group))
      .sort((a, b) => b.value - a.value)
      .map((c, i) => ({
        name: itemLabel(c),
        value: c.value,
        color: activeTab === 'net_worth' ? (SLICE_COLORS[i] ?? OTHER_SLICE_COLOR) : c.color,
      }))
  })()

  // Outer ring — the top slices individually, with the long tail folded into a
  // single "Other". Capping the slice count is what keeps them tellable apart;
  // the full breakdown stays one click away in the legend's "+N more" popover.
  const outerDonutData = (() => {
    if (compositionDetail.length === 0) return []
    const LIMIT = SLICE_COLORS.length
    const top = compositionDetail.slice(0, LIMIT)
    const rest = compositionDetail.slice(LIMIT)
    const result: { name: string; value: number; color: string }[] =
      top.map((d) => ({ name: d.name, value: d.value, color: d.color }))
    if (rest.length > 0) {
      result.push({
        name: t('reports.other'),
        value: Math.round(rest.reduce((s, d) => s + d.value, 0) * 100) / 100,
        color: OTHER_SLICE_COLOR,
      })
    }
    return result
  })()

  return (
    <div>
      <PageHeader
        section={t('reports.section')}
        title={t(currentTab.labelKey)}
        action={
          <div className="flex items-center gap-2">
            {isCashFlow && (
              <div
                className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${
                  cashFlowBaseline
                    ? 'border-primary/40 bg-primary/10 text-primary'
                    : 'border-border bg-card text-muted-foreground'
                }`}
              >
                <button
                  type="button"
                  onClick={() => setCashFlowBaseline((v) => !v)}
                  className="flex items-center gap-2 hover:text-foreground transition-colors"
                  aria-pressed={cashFlowBaseline}
                >
                  <span
                    className={`relative inline-flex h-3.5 w-6 shrink-0 items-center rounded-full transition-colors ${
                      cashFlowBaseline ? 'bg-primary' : 'bg-muted'
                    }`}
                  >
                    <span
                      className={`inline-block h-2.5 w-2.5 transform rounded-full bg-white transition-transform ${
                        cashFlowBaseline ? 'translate-x-3' : 'translate-x-0.5'
                      }`}
                    />
                  </span>
                  {t('reports.includeEstimate')}
                </button>
                <span
                  title={t('reports.includeEstimateHelp')}
                  aria-label={t('reports.includeEstimateHelp')}
                  className="inline-flex cursor-help"
                >
                  <HelpCircle className="h-3.5 w-3.5 opacity-60" />
                </span>
              </div>
            )}
            <div className="flex items-center rounded-lg border border-border bg-card overflow-hidden">
              {rangeOptions.map((opt) => (
                <button
                  key={opt.key}
                  onClick={() => setRangeKey(opt.key)}
                  className={`px-3 py-1.5 text-xs font-semibold transition-colors ${
                    rangeKey === opt.key
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                  }`}
                >
                  {t(`reports.${RANGE_LABELS[opt.key]}`)}
                </button>
              ))}
            </div>
            <div className="flex items-center rounded-lg border border-border bg-card overflow-hidden">
              {intervalOptions.map((opt) => (
                <button
                  key={opt.key}
                  onClick={() => setInterval(opt.value)}
                  className={`px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                    interval === opt.value
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                  }`}
                >
                  {t(`reports.${INTERVAL_LABELS[opt.key]}`)}
                </button>
              ))}
            </div>
          </div>
        }
      />

      {/* Tab Bar */}
      <div className="flex items-center gap-1 mb-5 border-b border-border">
        {REPORT_TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => { if (tab.enabled) handleSelectTab(tab.key) }}
            disabled={!tab.enabled}
            className={`relative px-4 py-2.5 text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'text-foreground'
                : tab.enabled
                  ? 'text-muted-foreground hover:text-foreground'
                  : 'text-muted-foreground/50 cursor-not-allowed'
            }`}
          >
            {t(tab.labelKey)}
            {!tab.enabled && (
              <span className="ml-1.5 text-[10px] text-muted-foreground/50">
                {t('reports.comingSoon')}
              </span>
            )}
            {activeTab === tab.key && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-primary rounded-full" />
            )}
          </button>
        ))}
      </div>

      {/* Hero Card */}
      <div className="bg-card rounded-xl border border-border shadow-sm mb-5">
        <div className="px-5 py-4">
          {isLoading ? (
            <div className="flex items-center gap-8">
              <Skeleton className="h-16 w-48" />
              <div className="flex gap-6">
                <Skeleton className="h-12 w-28" />
                <Skeleton className="h-12 w-28" />
                <Skeleton className="h-12 w-28" />
              </div>
            </div>
          ) : (
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-0.5 uppercase tracking-wider">
                  {t(currentTab.labelKey)}
                </p>
                <div className="flex items-baseline gap-3">
                  <p className="text-3xl font-bold tabular-nums text-foreground">
                    {mask(formatCurrency(summary?.primary_value ?? 0, userCurrency, locale))}
                  </p>
                  {summary?.change_percent !== null && summary?.change_percent !== undefined && (
                    <span className={`text-sm font-semibold tabular-nums ${changeColor}`}>
                      {changePrefix}{summary.change_percent.toFixed(1)}%
                    </span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {mask(`${changePrefix}${formatCurrency(summary?.change_amount ?? 0, userCurrency, locale)}`)}
                  {' '}{t(meta?.type === 'cash_flow' ? 'reports.vsToday' : 'reports.vsStart')}
                </p>
              </div>
              <div className="flex flex-wrap gap-6">
                {breakdownData.map((b) => (
                  <div key={b.key} className="min-w-0">
                    <div className="flex items-center gap-1.5 mb-0.5">
                      <div
                        className="w-2.5 h-2.5 rounded-full shrink-0"
                        style={{ backgroundColor: b.color }}
                      />
                      <p className="text-xs font-medium text-muted-foreground">
                        {t(`reports.${b.key}`, { defaultValue: b.label })}
                      </p>
                    </div>
                    <p className="text-lg font-bold tabular-nums text-foreground">
                      {mask(formatCurrency(b.value, userCurrency, locale))}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Main Trend Chart */}
      <div className="bg-card rounded-xl border border-border shadow-sm mb-5">
        <div className="px-5 pt-5 pb-2 flex items-center justify-between">
          <p className="text-sm font-semibold text-foreground">
            {t(currentTab.labelKey)} · {t('reports.trend')}
          </p>
          {meta && (
            <div className="flex items-center gap-3">
              {meta.type === 'net_worth' ? (
                <div className="flex items-center gap-1.5">
                  <div className="w-2 h-2 rounded-full" style={{ backgroundColor: '#6366F1' }} />
                  <span className="text-[11px] text-muted-foreground">
                    {t('reports.netWorth')}
                  </span>
                </div>
              ) : (
                meta.series_keys.map((key) => (
                  <div key={key} className="flex items-center gap-1.5">
                    <div
                      className="w-2 h-2 rounded-full"
                      style={{ backgroundColor: colorMap[key] || '#6366F1' }}
                    />
                    <span className="text-[11px] text-muted-foreground">
                      {t(`reports.${key}`, { defaultValue: key })}
                    </span>
                  </div>
                ))
              )}
              {meta.type === 'income_expenses' && (
                <div className="flex items-center gap-1.5">
                  <div className="w-3 h-0 border-t-2 border-dashed" style={{ borderColor: '#6366F1' }} />
                  <span className="text-[11px] text-muted-foreground">
                    {t('reports.netIncome')}
                  </span>
                </div>
              )}
              {meta.type === 'cash_flow' && (
                <div className="flex items-center gap-1.5">
                  <div className="w-3 h-0 border-t-2 border-dashed" style={{ borderColor: '#6366F1' }} />
                  <span className="text-[11px] text-muted-foreground">
                    {meta.baseline_active ? t('reports.forecastBaseline') : t('reports.forecast')}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
        <div className="px-1 pb-4" style={{ height: 320 }}>
          {isLoading ? (
            <div className="px-4">
              <Skeleton className="h-full w-full" />
            </div>
          ) : chartData.length > 0 ? (
            meta?.type === 'cash_flow' ? (() => {
              const startingBalance = summary?.breakdowns.find((b) => b.key === 'startingBalance')?.value ?? 0
              return (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="cashFlowGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#6366F1" stopOpacity={0.2} />
                        <stop offset="95%" stopColor="#6366F1" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                      axisLine={false}
                      tickLine={false}
                      interval="preserveStartEnd"
                    />
                    <YAxis
                      tickFormatter={(v) => {
                        if (privacyMode) return ''
                        if (v === 0) return '0'
                        return formatCompact(v, userCurrency, locale)
                      }}
                      tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                      axisLine={false}
                      tickLine={false}
                      width={64}
                      tickCount={5}
                    />
                    <Tooltip
                      content={({ active, payload, label }) => {
                        if (!active || !payload || payload.length === 0) return null
                        const point = payload[0].payload as Record<string, number>
                        const balance = point.value ?? 0
                        const inflow = point.inflow ?? 0
                        const outflow = point.outflow ?? 0
                        return (
                          <div style={tooltipStyle} className="px-3 py-2">
                            <p className="text-xs font-medium mb-1">{label}</p>
                            <p className="text-xs" style={{ color: '#6366F1' }}>
                              {t('reports.balance', { defaultValue: 'Balance' })}:{' '}
                              {privacyMode ? MASK : formatCurrency(balance, userCurrency, locale)}
                            </p>
                            {inflow > 0 && (
                              <p className="text-xs" style={{ color: '#10B981' }}>
                                {t('reports.inflow')}:{' '}
                                {privacyMode ? MASK : `+${formatCurrency(inflow, userCurrency, locale)}`}
                              </p>
                            )}
                            {outflow > 0 && (
                              <p className="text-xs" style={{ color: '#F43F5E' }}>
                                {t('reports.outflow')}:{' '}
                                {privacyMode ? MASK : `-${formatCurrency(outflow, userCurrency, locale)}`}
                              </p>
                            )}
                          </div>
                        )
                      }}
                    />
                    <ReferenceLine
                      y={startingBalance}
                      stroke="var(--muted-foreground)"
                      strokeDasharray="4 4"
                      strokeOpacity={0.5}
                    />
                    {forecastStart && (
                      <ReferenceLine
                        x={forecastStart}
                        stroke="var(--muted-foreground)"
                        strokeDasharray="3 3"
                        strokeOpacity={0.6}
                        label={{
                          value: t('reports.today'),
                          position: 'insideTopRight',
                          fill: 'var(--muted-foreground)',
                          fontSize: 10,
                        }}
                      />
                    )}
                    <Area
                      type="monotone"
                      dataKey="valuePast"
                      stroke="#6366F1"
                      strokeWidth={2.5}
                      fill="url(#cashFlowGrad)"
                      dot={false}
                      activeDot={{ r: 4, fill: '#6366F1' }}
                      isAnimationActive={false}
                      connectNulls={false}
                    />
                    <Area
                      type="monotone"
                      dataKey="valueForecast"
                      stroke="#6366F1"
                      strokeWidth={2.5}
                      strokeDasharray="6 3"
                      fill="url(#cashFlowGrad)"
                      fillOpacity={0.4}
                      dot={false}
                      activeDot={{ r: 4, fill: '#6366F1' }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              )
            })() : meta?.type === 'income_expenses' ? (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                  axisLine={false}
                  tickLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis
                  tickFormatter={(v) => {
                    if (privacyMode) return ''
                    if (v === 0) return '0'
                    return formatCompact(v, userCurrency, locale)
                  }}
                  tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                  axisLine={false}
                  tickLine={false}
                  width={64}
                  tickCount={5}
                />
                <Tooltip
                  formatter={(value?: number, name?: string) => [
                    privacyMode ? MASK : formatCurrency(value ?? 0, userCurrency, locale),
                    name === 'value'
                      ? t('reports.netIncome')
                      : t(`reports.${name ?? ''}`, { defaultValue: name ?? '' }),
                  ]}
                  labelFormatter={(label) => label}
                  contentStyle={tooltipStyle}
                />
                <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="3 3" />
                <Bar dataKey="income" fill="#10B981" radius={[4, 4, 0, 0]} maxBarSize={24} />
                <Bar dataKey="expenses" fill="#F43F5E" radius={[4, 4, 0, 0]} maxBarSize={24} />
                <Line
                  type="monotone"
                  dataKey="value"
                  stroke="#6366F1"
                  strokeWidth={2}
                  strokeDasharray="6 3"
                  dot={false}
                  activeDot={{ r: 4, fill: '#6366F1' }}
                />
              </ComposedChart>
            </ResponsiveContainer>
            ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="netWorthGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366F1" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#6366F1" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                  axisLine={false}
                  tickLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis
                  tickFormatter={(v) => {
                    if (privacyMode) return ''
                    if (v === 0) return '0'
                    return formatCompact(v, userCurrency, locale)
                  }}
                  tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                  axisLine={false}
                  tickLine={false}
                  width={64}
                  tickCount={5}
                />
                <Tooltip
                  content={({ active, payload, label }) => {
                    if (!active || !payload || payload.length === 0) return null
                    const point = payload[0]?.payload as Record<string, number | null>
                    const value = (payload[0]?.value as number) ?? 0
                    const change = point.change ?? null
                    const changeSign = change !== null && change >= 0 ? '+' : ''
                    const changeColor = change !== null ? (change >= 0 ? '#10B981' : '#F43F5E') : ''
                    return (
                      <div style={tooltipStyle} className="px-3 py-2">
                        <p className="text-xs font-medium mb-1">{label}</p>
                        <p className="text-xs" style={{ color: '#6366F1' }}>
                          {t(currentTab.labelKey)}:{' '}
                          {privacyMode ? MASK : formatCurrency(value, userCurrency, locale)}
                        </p>
                        {change !== null && (
                          <p className="text-xs" style={{ color: changeColor }}>
                            {t('reports.change')}:{' '}
                            {privacyMode ? MASK : `${changeSign}${formatCurrency(change, userCurrency, locale)}`}
                          </p>
                        )}
                      </div>
                    )
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke="#6366F1"
                  strokeWidth={2.5}
                  fill="url(#netWorthGrad)"
                  dot={false}
                  activeDot={{ r: 4, fill: '#6366F1' }}
                />
              </AreaChart>
            </ResponsiveContainer>
            )
          ) : (
            <p className="text-muted-foreground text-sm text-center py-16">
              {t('reports.noData')}
            </p>
          )}
        </div>
      </div>

      {/* Breakdown: Donut + Grouped Bar */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 items-start">
        {/* Composition widget — summary ring + ranked, labelled detail bars */}
        <div className="bg-card rounded-xl border border-border shadow-sm">
          <div className="px-5 pt-4 pb-2 flex items-center justify-between gap-2">
            <p className="text-sm font-semibold text-foreground shrink-0">{t('reports.composition')}</p>
            <div className="flex items-stretch rounded-lg border border-border bg-muted/30 overflow-hidden">
              {compositionOptions.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  onClick={() => setCompositionView(opt)}
                  className={`px-2 py-1 text-[11px] font-semibold text-center whitespace-nowrap transition-colors ${
                    compositionView === opt
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                  }`}
                >
                  {t(`reports.${opt}`)}
                </button>
              ))}
            </div>
          </div>
          <div className="px-1 pb-4">
            {isLoading ? (
              <div className="flex flex-col items-center px-4 py-2">
                <div className="relative" style={{ width: 200, height: 200 }}>
                  <Skeleton className="w-full h-full rounded-full" />
                  <div
                    className="absolute flex flex-col items-center justify-center gap-1 rounded-full bg-card"
                    style={{ width: 110, height: 110, top: '50%', left: '50%', transform: 'translate(-50%, -50%)' }}
                  >
                    <Skeleton className="h-2 w-12" />
                    <Skeleton className="h-4 w-16" />
                  </div>
                </div>
                <div className="flex flex-wrap justify-center gap-x-3 gap-y-1.5 mt-3">
                  {Array.from({ length: activeTab === 'net_worth' ? 3 : 2 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-1.5">
                      <Skeleton className="w-2 h-2 rounded-full shrink-0" />
                      <Skeleton className="h-2 w-14" />
                    </div>
                  ))}
                </div>
              </div>
            ) : innerDonutData.length > 0 ? (
                (() => {
                  const hasOuter = outerDonutData.length > 0
                  const donutTotal = innerDonutData.reduce((s, d) => s + d.value, 0)
                  return (
                    <div className="flex flex-col items-center">
                      <div className="relative" style={{ width: 200, height: 200 }}>
                        <PieChart width={200} height={200}>
                            <Pie
                              data={innerDonutData}
                              cx="50%"
                              cy="50%"
                              innerRadius={55}
                              outerRadius={hasOuter ? 63 : 85}
                              paddingAngle={hasOuter ? 0 : 3}
                              dataKey="value"
                              stroke="var(--card)"
                              strokeWidth={hasOuter ? 2 : 0}
                            >
                              {innerDonutData.map((entry, idx) => (
                                <Cell key={idx} fill={entry.color} />
                              ))}
                            </Pie>
                            {hasOuter && (
                              <Pie
                                data={outerDonutData}
                                cx="50%"
                                cy="50%"
                                innerRadius={64}
                                outerRadius={90}
                                paddingAngle={0}
                                dataKey="value"
                                stroke="var(--card)"
                                strokeWidth={2}
                              >
                                {outerDonutData.map((entry, idx) => (
                                  <Cell key={idx} fill={entry.color} />
                                ))}
                              </Pie>
                            )}
                            <Tooltip
                              content={({ active, payload }) => {
                                if (!active || !payload?.length) return null
                                const entry = payload[0]
                                const v = (entry.value as number) ?? 0
                                const pct = donutTotal > 0 ? ((v / donutTotal) * 100).toFixed(1) : '0'
                                const rawName = (entry.name as string) ?? ''
                                const displayName = rawName.length > 50 ? rawName.slice(0, 47) + '…' : rawName
                                return (
                                  <div style={{ ...tooltipStyle, padding: '8px 12px', zIndex: 10 }}>
                                    <p className="text-xs font-semibold mb-1">{displayName}</p>
                                    <p className="text-xs">
                                      {privacyMode ? MASK : `${formatCurrency(v, userCurrency, locale)} (${pct}%)`}
                                    </p>
                                  </div>
                                )
                              }}
                              wrapperStyle={{ zIndex: 10 }}
                              offset={20}
                            />
                        </PieChart>
                        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none" style={{ zIndex: 0 }}>
                          <span className="text-[10px] text-muted-foreground">
                            {compositionView === 'assetsAndAccounts' ? t('reports.youOwn', { defaultValue: 'You Own' })
                              : compositionView === 'liabilities' ? t('reports.youOwe', { defaultValue: 'You Owe' })
                              : compositionView === 'byIncome' ? t('reports.income')
                              : compositionView === 'byExpenses' ? t('reports.expenses')
                              : meta?.type === 'income_expenses' ? t('reports.netIncome')
                              : t(currentTab.labelKey)}
                          </span>
                          <span className="text-base font-bold text-foreground tabular-nums">
                            {mask(formatCompact(
                              compositionView === 'netWorth' || compositionView === 'net' || !compositionView
                                ? meta?.type === 'cash_flow'
                                  ? (summary?.change_amount ?? 0)
                                  : (summary?.primary_value ?? 0)
                                : innerDonutData.reduce((s, d) => s + d.value, 0),
                              userCurrency, locale
                            ))}
                          </span>
                        </div>
                      </div>
                      <div key={compositionView} className="flex flex-col items-center gap-1 px-3 mt-1 w-full">
                        {/* Inner-ring (summary) legend only when there is no detailed
                            outer ring — otherwise the toggle + center label already
                            name it, and the detailed legend below carries the colours. */}
                        {!hasOuter && (
                          <div className="flex flex-wrap justify-center gap-x-3 gap-y-1">
                            {innerDonutData.map((d, i) => (
                              <div key={`${i}-${d.name}`} className="flex items-center gap-1.5">
                                <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: d.color }} />
                                <span className="text-[11px] text-muted-foreground whitespace-nowrap">
                                  {d.name.length > 30 ? d.name.slice(0, 27) + '…' : d.name}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                        {hasOuter && (() => {
                          const visible = outerDonutData.slice(0, 6)
                          const hiddenCount = compositionDetail.length - visible.length
                          return (
                            <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 items-center">
                              {visible.map((d, i) => (
                                <div key={`${i}-${d.name}`} className="flex items-center gap-1.5">
                                  <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: d.color }} />
                                  <span className="text-[11px] text-muted-foreground whitespace-nowrap">
                                    {d.name.length > 30 ? d.name.slice(0, 27) + '…' : d.name}
                                  </span>
                                </div>
                              ))}
                              {hiddenCount > 0 && (
                                <Popover>
                                  <PopoverTrigger asChild>
                                    <button
                                      type="button"
                                      className="text-[11px] font-semibold text-primary hover:text-primary/80 transition-colors"
                                    >
                                      +{hiddenCount} more
                                    </button>
                                  </PopoverTrigger>
                                  <PopoverContent align="center" side="top" sideOffset={8} className="w-64 p-3">
                                    <p className="text-xs font-semibold text-foreground mb-2">
                                      {t('reports.composition')}
                                    </p>
                                    <div className="flex flex-col gap-1.5 max-h-72 overflow-y-auto pr-2">
                                      {compositionDetail.map((d, i) => {
                                        const pct = donutTotal > 0 ? ((d.value / donutTotal) * 100).toFixed(1) : '0'
                                        return (
                                          <div key={`${i}-${d.name}`} className="flex items-center gap-2">
                                            <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: d.color }} />
                                            <span className="text-[11px] text-muted-foreground flex-1 truncate">
                                              {d.name.length > 25 ? d.name.slice(0, 22) + '…' : d.name}
                                            </span>
                                            <span className="text-[11px] tabular-nums text-foreground whitespace-nowrap">
                                              {mask(formatCompact(d.value, userCurrency, locale))}
                                              <span className="text-muted-foreground ml-1">({pct}%)</span>
                                            </span>
                                          </div>
                                        )
                                      })}
                                    </div>
                                  </PopoverContent>
                                </Popover>
                              )}
                            </div>
                          )
                        })()}
                      </div>
                    </div>
                  )
                })()
              ) : (
                <p className="text-muted-foreground text-sm text-center py-16">{t('reports.noData')}</p>
              )
            }
          </div>
        </div>

        {/* Evolution / Category Sparklines */}
        <div className="lg:col-span-2 bg-card rounded-xl border border-border shadow-sm">
          <div className="px-5 pt-5 pb-2 flex items-center justify-between">
            <p className="text-sm font-semibold text-foreground">
              {meta?.type === 'income_expenses'
                ? t('reports.categoryTrends')
                : meta?.type === 'cash_flow'
                  ? t('reports.inflowOutflow')
                  : t('reports.evolution')}
            </p>
            {meta?.type === 'income_expenses' && (() => {
              const groupKey = sparklineView === 'byIncome' ? 'income' : 'expenses'
              const allItems = (data?.category_trend ?? []).filter((c) => c.group === groupKey)
              const totalPages = Math.ceil(allItems.length / 6)
              const hasPagination = totalPages > 1
              return (
                <div className="flex items-center gap-2">
                  {hasPagination && (
                    <div className="flex items-center gap-0.5">
                      <button
                        onClick={() => setSparklinePage((p) => Math.max(0, p - 1))}
                        disabled={sparklinePage === 0}
                        className="p-1 rounded text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
                      </button>
                      <button
                        onClick={() => setSparklinePage((p) => Math.min(totalPages - 1, p + 1))}
                        disabled={sparklinePage >= totalPages - 1}
                        className="p-1 rounded text-muted-foreground hover:text-foreground disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>
                      </button>
                    </div>
                  )}
                  <div className="flex items-center rounded-lg border border-border bg-muted/30 overflow-hidden">
                    {(['byExpenses', 'byIncome'] as const).map((opt) => (
                      <button
                        key={opt}
                        onClick={() => { setSparklineView(opt); setSparklinePage(0) }}
                        className={`px-2.5 py-1 text-[11px] font-semibold transition-colors ${
                          sparklineView === opt
                            ? 'bg-primary text-primary-foreground'
                            : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                        }`}
                      >
                        {t(`reports.${opt}`)}
                      </button>
                    ))}
                  </div>
                </div>
              )
            })()}
          </div>
          {meta?.type === 'income_expenses' ? (
            <div className="pb-4 overflow-hidden">
              {isLoading ? (
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 px-4">
                  {Array.from({ length: 6 }).map((_, i) => (
                    <Skeleton key={i} className="h-20 w-full" />
                  ))}
                </div>
              ) : (() => {
                const groupKey = sparklineView === 'byIncome' ? 'income' : 'expenses'
                const allGroupItems: CategoryTrendItem[] = (data?.category_trend ?? []).filter(
                  (c) => c.group === groupKey
                )
                if (allGroupItems.length === 0) {
                  return (
                    <p className="text-muted-foreground text-sm text-center py-16">
                      {t('reports.noData')}
                    </p>
                  )
                }
                const totalPages = Math.ceil(allGroupItems.length / 6)
                const pages = Array.from({ length: totalPages }, (_, i) =>
                  allGroupItems.slice(i * 6, i * 6 + 6)
                )
                return (
                  <div
                    className="flex"
                    style={{
                      transform: `translateX(-${sparklinePage * 100}%)`,
                      transition: 'transform 300ms cubic-bezier(0.4, 0, 0.2, 1)',
                    }}
                  >
                    {pages.map((pageItems, pageIdx) => (
                      <div
                        key={pageIdx}
                        className="grid grid-cols-2 sm:grid-cols-3 gap-3 w-full shrink-0 px-4"
                      >
                        {pageItems.map((item) => {
                          const sparkData = item.series.map((s) => ({ date: s.date, v: s.value }))
                          const gradId = `grad-${item.key}-${item.group}-p${pageIdx}`
                          return (
                            <div
                              key={`${item.key}-${item.group}`}
                              className="rounded-lg border border-border/50 bg-muted/20 px-3 py-2"
                            >
                              <div className="flex items-center gap-1.5 mb-0.5">
                                <div
                                  className="w-2 h-2 rounded-full shrink-0"
                                  style={{ backgroundColor: item.color }}
                                />
                                <span className="text-[11px] text-muted-foreground truncate">
                                  {item.key === 'uncategorized' ? t('reports.uncategorized') : item.key === 'other' ? t('reports.other') : item.label}
                                </span>
                              </div>
                              <p className="text-xs font-bold tabular-nums text-foreground mb-1">
                                {mask(formatCompact(item.total, userCurrency, locale))}
                              </p>
                              <div style={{ height: 48 }}>
                                <ResponsiveContainer width="100%" height="100%">
                                  <AreaChart data={sparkData} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
                                    <defs>
                                      <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor={item.color} stopOpacity={0.3} />
                                        <stop offset="95%" stopColor={item.color} stopOpacity={0.02} />
                                      </linearGradient>
                                    </defs>
                                    <XAxis dataKey="date" hide />
                                    <Tooltip
                                      formatter={(value?: number) => [
                                        privacyMode ? MASK : formatCurrency(value ?? 0, userCurrency, locale),
                                        item.label,
                                      ]}
                                      labelFormatter={(label) => label}
                                      contentStyle={{ ...tooltipStyle, padding: '4px 8px' }}
                                    />
                                    <Area
                                      type="monotone"
                                      dataKey="v"
                                      stroke={item.color}
                                      strokeWidth={1.5}
                                      fill={`url(#${gradId})`}
                                      dot={false}
                                      activeDot={{ r: 2, fill: item.color }}
                                    />
                                  </AreaChart>
                                </ResponsiveContainer>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    ))}
                  </div>
                )
              })()}
            </div>
          ) : (
          <div className="px-1 pb-4" style={{ height: 280 }}>
            {isLoading ? (
              <div className="px-4">
                <Skeleton className="h-full w-full" />
              </div>
            ) : chartData.length > 0 && meta ? (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }} stackOffset="sign">
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                    axisLine={false}
                    tickLine={false}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    tickFormatter={(v) => {
                      if (privacyMode) return ''
                      if (v === 0) return '0'
                      return formatCompact(v, userCurrency, locale)
                    }}
                    tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                    axisLine={false}
                    tickLine={false}
                    width={64}
                    tickCount={5}
                  />
                  <ReferenceLine y={0} stroke="var(--border)" strokeWidth={1} />
                  <Tooltip
                    content={({ active, payload, label }) => {
                      if (!active || !payload) return null
                      const items = payload.filter((p) => p.value !== null && p.value !== undefined && (p.value as number) !== 0)
                      if (items.length === 0) return null
                      return (
                        <div style={tooltipStyle} className="px-3 py-2">
                          <p className="text-xs font-medium mb-1">{label}</p>
                          {items.map((p) => (
                            <p key={p.dataKey as string} className="text-xs" style={{ color: p.color }}>
                              {t(`reports.${p.dataKey}`, { defaultValue: p.name })}:{' '}
                              {privacyMode ? MASK : formatCurrency(p.value as number, userCurrency, locale)}
                            </p>
                          ))}
                        </div>
                      )
                    }}
                  />
                  <Legend
                    iconType="circle"
                    iconSize={8}
                    wrapperStyle={{ fontSize: '12px', paddingTop: '8px' }}
                    formatter={(value: string) => t(`reports.${value}`, { defaultValue: value })}
                  />
                  {(() => {
                    const isNetWorth = meta.type === 'net_worth'
                    const allSeries = meta.type === 'cash_flow'
                      ? [
                          { key: 'inflow', color: '#10B981' },
                          { key: 'outflow', color: '#F43F5E' },
                        ]
                      : meta.series_keys.map((k) => ({ key: k, color: colorMap[k] || '#6366F1' }))
                    const filteredSeries = allSeries.filter(({ key }) =>
                      chartData.some((d) => { const v = d[key]; return typeof v === 'number' && v !== 0 })
                    )
                    const positiveKeys = isNetWorth ? filteredSeries.filter(({ key }) => !NEGATIVE_SERIES.has(key)) : filteredSeries
                    const negativeKeys = isNetWorth ? filteredSeries.filter(({ key }) => NEGATIVE_SERIES.has(key)) : []
                    const lastPositiveKey = positiveKeys.at(-1)?.key ?? null
                    const lastNegativeKey = negativeKeys.at(-1)?.key ?? null
                    return filteredSeries.map(({ key, color }) => {
                      let radius: [number, number, number, number] = [0, 0, 0, 0]
                      if (isNetWorth && NEGATIVE_SERIES.has(key) && key === lastNegativeKey) {
                        radius = [4, 4, 0, 0]
                      } else if (key === lastPositiveKey) {
                        radius = [4, 4, 0, 0]
                      }
                      return (
                        <Bar
                          key={key}
                          dataKey={key}
                          stackId="stack"
                          fill={color}
                          radius={radius}
                          maxBarSize={32}
                        />
                      )
                    })
                  })()}
                  {meta.type === 'net_worth' && (
                    <Line
                      type="monotone"
                      dataKey="value"
                      name={t('reports.netWorth', { defaultValue: 'Net Worth' })}
                      stroke="#10B981"
                      strokeWidth={2}
                      strokeDasharray="6 3"
                      dot={false}
                      activeDot={{ r: 4, fill: '#10B981' }}
                      isAnimationActive={false}
                    />
                  )}
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-muted-foreground text-sm text-center py-16">
                {t('reports.noData')}
              </p>
            )}
          </div>
          )}
        </div>
      </div>
    </div>
  )
}
