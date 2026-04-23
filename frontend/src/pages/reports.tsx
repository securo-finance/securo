import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import {
  AreaChart,
  Area,
  BarChart,
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
import { reports } from '@/lib/api'
import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/page-header'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import type { ReportResponse, CategoryTrendItem } from '@/types'

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

const COMPOSITION_TOP_N = 6

const RANGE_OPTIONS = [
  { key: '6m', months: 6 },
  { key: '1y', months: 12 },
  { key: '2y', months: 24 },
] as const

const INTERVAL_OPTIONS = [
  { key: 'daily', value: 'daily' },
  { key: 'weekly', value: 'weekly' },
  { key: 'monthly', value: 'monthly' },
  { key: 'yearly', value: 'yearly' },
] as const

const INTERVAL_LABELS: Record<string, string> = {
  daily: 'intervalDaily',
  weekly: 'intervalWeekly',
  monthly: 'intervalMonthly',
  yearly: 'intervalYearly',
}

const RANGE_LABELS: Record<string, string> = {
  '6m': 'range6m',
  '1y': 'range1y',
  '2y': 'range2y',
}

interface ReportTab {
  key: string
  labelKey: string
  fetch: (months: number, interval: string) => Promise<ReportResponse>
  enabled: boolean
}

const REPORT_TABS: ReportTab[] = [
  { key: 'net_worth', labelKey: 'reports.netWorth', fetch: (m, i) => reports.netWorth(m, i), enabled: true },
  { key: 'income_expenses', labelKey: 'reports.incomeExpenses', fetch: (m, i) => reports.incomeExpenses(m, i), enabled: true },
  { key: 'cash_flow', labelKey: 'reports.cashFlow', fetch: () => Promise.reject(), enabled: false },
]

export default function ReportsPage() {
  const { t, i18n } = useTranslation()
  const { mask, privacyMode, MASK } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = i18n.language === 'en' ? 'en-US' : i18n.language

  const [months, setMonths] = useState(12)
  const [interval, setInterval] = useState('monthly')
  const [activeTab, setActiveTab] = useState('net_worth')
  const [compositionView, setCompositionView] = useState<string>('summary')
  const [sparklineView, setSparklineView] = useState<'byExpenses' | 'byIncome'>('byExpenses')
  const [sparklinePage, setSparklinePage] = useState(0)

  const currentTab = REPORT_TABS.find((tab) => tab.key === activeTab) ?? REPORT_TABS[0]

  const { data, isLoading } = useQuery<ReportResponse>({
    queryKey: ['reports', activeTab, months, interval],
    queryFn: () => currentTab.fetch(months, interval),
    enabled: currentTab.enabled,
  })

  const summary = data?.summary
  const trend = data?.trend ?? []
  const meta = data?.meta

  const chartData = trend.map((dp) => ({
    date: dp.date,
    value: dp.value,
    ...dp.breakdowns,
  } as Record<string, string | number>))

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

  const tooltipItemStyle = { color: 'var(--foreground)' }

  // Composition view options per report type
  const compositionOptions = meta?.type === 'income_expenses'
    ? ['summary', 'byIncome', 'byExpenses'] as const
    : ['summary', 'detailed'] as const

  // Build donut data based on composition view
  const composition = data?.composition ?? []

  const donutData = (() => {
    if (compositionView === 'summary' || composition.length === 0) {
      return breakdownData
        .filter((b) => b.value > 0 && b.key !== 'netIncome')
        .map((b) => ({
          name: t(`reports.${b.key}`, { defaultValue: b.label }),
          value: b.value,
          color: b.color,
        }))
    }

    let items = composition
    if (compositionView === 'byIncome') {
      items = composition.filter((c) => c.group === 'income')
    } else if (compositionView === 'byExpenses') {
      items = composition.filter((c) => c.group === 'expenses')
    }

    // Sort descending, take top N, bucket the rest into "Other"
    const sorted = [...items].sort((a, b) => b.value - a.value)
    const top = sorted.slice(0, COMPOSITION_TOP_N)
    const rest = sorted.slice(COMPOSITION_TOP_N)
    const otherValue = rest.reduce((sum, c) => sum + c.value, 0)

    const result = top.map((c) => ({
      name: c.key === 'uncategorized' ? t('reports.uncategorized') : c.label,
      value: c.value,
      color: c.color,
    }))
    if (otherValue > 0) {
      result.push({ name: t('reports.other'), value: Math.round(otherValue * 100) / 100, color: '#6B7280' })
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
            <div className="flex items-center rounded-lg border border-border bg-card overflow-hidden">
              {RANGE_OPTIONS.map((opt) => (
                <button
                  key={opt.key}
                  onClick={() => setMonths(opt.months)}
                  className={`px-3 py-1.5 text-xs font-semibold transition-colors ${
                    months === opt.months
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                  }`}
                >
                  {t(`reports.${RANGE_LABELS[opt.key]}`)}
                </button>
              ))}
            </div>
            <div className="flex items-center rounded-lg border border-border bg-card overflow-hidden">
              {INTERVAL_OPTIONS.map((opt) => (
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
            onClick={() => { if (tab.enabled) { setActiveTab(tab.key); setCompositionView('summary') } }}
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
                  {' '}{t('reports.vsStart')}
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
              {meta.series_keys.map((key) => (
                <div key={key} className="flex items-center gap-1.5">
                  <div
                    className="w-2 h-2 rounded-full"
                    style={{ backgroundColor: colorMap[key] || '#6366F1' }}
                  />
                  <span className="text-[11px] text-muted-foreground">
                    {t(`reports.${key}`, { defaultValue: key })}
                  </span>
                </div>
              ))}
              {meta.type === 'income_expenses' && (
                <div className="flex items-center gap-1.5">
                  <div className="w-3 h-0 border-t-2 border-dashed" style={{ borderColor: '#6366F1' }} />
                  <span className="text-[11px] text-muted-foreground">
                    {t('reports.netIncome')}
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
            meta?.type === 'income_expenses' ? (
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
                  formatter={(value?: number, name?: string) => [
                    privacyMode ? MASK : formatCurrency(value ?? 0, userCurrency, locale),
                    name === 'value'
                      ? t(currentTab.labelKey)
                      : t(`reports.${name ?? ''}`, { defaultValue: name ?? '' }),
                  ]}
                  labelFormatter={(label) => label}
                  contentStyle={tooltipStyle}
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
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Donut Chart — Current Composition */}
        <div className="bg-card rounded-xl border border-border shadow-sm">
          <div className="px-5 pt-4 pb-2 flex items-center justify-between">
            <p className="text-sm font-semibold text-foreground">{t('reports.composition')}</p>
            <div className="flex items-center rounded-lg border border-border bg-muted/30 overflow-hidden">
              {compositionOptions.map((opt) => (
                <button
                  key={opt}
                  onClick={() => setCompositionView(opt)}
                  className={`px-2.5 py-1 text-[11px] font-semibold transition-colors ${
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
              <div className="px-4" style={{ height: 200 }}>
                <Skeleton className="h-full w-full" />
              </div>
            ) : donutData.length > 0 ? (
              (() => {
                const donutTotal = donutData.reduce((s, d) => s + d.value, 0)
                const centerLabel = compositionView === 'byIncome'
                  ? t('reports.income')
                  : compositionView === 'byExpenses'
                    ? t('reports.expenses')
                    : meta?.type === 'income_expenses'
                      ? t('reports.netIncome')
                      : t(currentTab.labelKey)
                const centerValue = compositionView === 'byIncome'
                  ? (summary?.breakdowns.find((b) => b.key === 'income')?.value ?? 0)
                  : compositionView === 'byExpenses'
                    ? (summary?.breakdowns.find((b) => b.key === 'expenses')?.value ?? 0)
                    : (summary?.primary_value ?? 0)
                return (
                  <div className="flex flex-col items-center">
                    <div className="relative" style={{ width: 200, height: 200 }}>
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie
                            data={donutData}
                            cx="50%"
                            cy="50%"
                            innerRadius={55}
                            outerRadius={85}
                            paddingAngle={3}
                            dataKey="value"
                            strokeWidth={0}
                          >
                            {donutData.map((entry, idx) => (
                              <Cell key={idx} fill={entry.color} />
                            ))}
                          </Pie>
                          <Tooltip
                            formatter={(value?: number, name?: string) => {
                              const v = value ?? 0
                              const pct = donutTotal > 0 ? ((v / donutTotal) * 100).toFixed(1) : '0'
                              return [
                                privacyMode ? MASK : `${formatCurrency(v, userCurrency, locale)} (${pct}%)`,
                                name,
                              ]
                            }}
                            contentStyle={{ ...tooltipStyle, zIndex: 10 }}
                            itemStyle={tooltipItemStyle}
                            wrapperStyle={{ zIndex: 10 }}
                            offset={20}
                          />
                        </PieChart>
                      </ResponsiveContainer>
                      {/* Center label — positioned absolutely over the SVG */}
                      <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none" style={{ zIndex: 0 }}>
                        <span className="text-[10px] text-muted-foreground">{centerLabel}</span>
                        <span className="text-base font-bold text-foreground tabular-nums">
                          {mask(formatCompact(centerValue, userCurrency, locale))}
                        </span>
                      </div>
                    </div>
                    {/* Custom legend */}
                    <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 px-3 mt-1">
                      {donutData.map((d) => (
                        <div key={d.name} className="flex items-center gap-1.5">
                          <div className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: d.color }} />
                          <span className="text-[11px] text-muted-foreground whitespace-nowrap">
                            {d.name}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )
              })()
            ) : (
              <p className="text-muted-foreground text-sm text-center py-16">
                {t('reports.noData')}
              </p>
            )}
          </div>
        </div>

        {/* Evolution / Category Sparklines */}
        <div className="lg:col-span-2 bg-card rounded-xl border border-border shadow-sm">
          <div className="px-5 pt-5 pb-2 flex items-center justify-between">
            <p className="text-sm font-semibold text-foreground">
              {meta?.type === 'income_expenses' ? t('reports.categoryTrends') : t('reports.evolution')}
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
                <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
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
                      if (!active || !payload) return null
                      const items = payload.filter((p) => (p.value as number) > 0)
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
                  {meta.series_keys
                    .filter((key) => chartData.some((d) => (d[key] as number) > 0))
                    .map((key, idx, arr) => (
                    <Bar
                      key={key}
                      dataKey={key}
                      stackId="stack"
                      fill={colorMap[key] || '#6366F1'}
                      radius={idx === arr.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
                      maxBarSize={32}
                    />
                  ))}
                </BarChart>
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
