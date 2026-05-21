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
import type { ReportResponse, CategoryTrendItem, ReportCompositionItem } from '@/types'

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

function hexToHsl(hex: string): [number, number, number] {
  const r = parseInt(hex.slice(1, 3), 16) / 255
  const g = parseInt(hex.slice(3, 5), 16) / 255
  const b = parseInt(hex.slice(5, 7), 16) / 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b)
  let h = 0
  const l = (max + min) / 2
  const d = max - min
  const s = d === 0 ? 0 : l > 0.5 ? d / (2 - max - min) : d / (max + min)
  if (d !== 0) {
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6
    else if (max === g) h = ((b - r) / d + 2) / 6
    else h = ((r - g) / d + 4) / 6
  }
  return [h * 360, s * 100, l * 100]
}

function hslToHex(h: number, s: number, l: number): string {
  h /= 360; s /= 100; l /= 100
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const hue2rgb = (t: number) => {
    if (t < 0) t += 1
    if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  const rv = s === 0 ? l : hue2rgb(h + 1 / 3)
  const gv = s === 0 ? l : hue2rgb(h)
  const bv = s === 0 ? l : hue2rgb(h - 1 / 3)
  return '#' + [rv, gv, bv].map((x) => Math.round(x * 255).toString(16).padStart(2, '0')).join('')
}

// Returns `count` colors from most vivid → most muted.
// The endpoint shifts hue by +50° and retains half the saturation so each
// group fades toward its own distinct soft color instead of a shared gray.
function buildGroupGradient(baseHex: string, count: number): string[] {
  if (count <= 0) return []
  if (count === 1) return [baseHex]
  const [h, s, l] = hexToHsl(baseHex)
  const targetH = h + 50
  const targetS = s * 0.50
  const targetL = Math.min(l + 18, 80)
  return Array.from({ length: count }, (_, i) => {
    const t = i / (count - 1)
    return hslToHex(h + (targetH - h) * t, s + (targetS - s) * t, l + (targetL - l) * t)
  })
}

const COMPOSITION_TOP_N = 10

type RangeOption = { key: string; months: number }

const HISTORICAL_RANGE_OPTIONS: readonly RangeOption[] = [
  { key: '6m', months: 6 },
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
  '12m': 'range12m',
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
  { key: 'cash_flow', labelKey: 'reports.cashFlow', fetch: (m, i) => reports.cashFlow(m, i), enabled: true },
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
  const [selectedNWBar, setSelectedNWBar] = useState<{ accounts: number; assets: number; liabilities: number; value: number; date: string; index: number } | null>(null)

  const currentTab = REPORT_TABS.find((tab) => tab.key === activeTab) ?? REPORT_TABS[0]

  const isCashFlow = activeTab === 'cash_flow'
  const rangeOptions = isCashFlow ? FORWARD_RANGE_OPTIONS : HISTORICAL_RANGE_OPTIONS
  const intervalOptions = isCashFlow ? CASH_FLOW_INTERVAL_OPTIONS : HISTORICAL_INTERVAL_OPTIONS

  const handleSelectTab = (key: string) => {
    setActiveTab(key)
    setCompositionView('summary')
    setSparklinePage(0)
    setSelectedNWBar(null)
    // Clamp months/interval to options supported by the new tab
    const nextRanges = key === 'cash_flow' ? FORWARD_RANGE_OPTIONS : HISTORICAL_RANGE_OPTIONS
    if (!nextRanges.some((r) => r.months === months)) {
      setMonths(key === 'cash_flow' ? 6 : 12)
    }
    const nextIntervals = key === 'cash_flow' ? CASH_FLOW_INTERVAL_OPTIONS : HISTORICAL_INTERVAL_OPTIONS
    if (!nextIntervals.some((i) => i.value === interval)) {
      setInterval(key === 'cash_flow' ? 'daily' : 'monthly')
    }
  }

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

  const netWorthSummaryData = chartData.map((d) => ({
    ...d,
    liabilitiesNeg: -((d.liabilities as number) ?? 0),
  })) as (Record<string, string | number> & { liabilitiesNeg: number })[]

  const nwTrendData = chartData.map((d, i) => {
    const current = d.value as number
    const prev = i > 0 ? (chartData[i - 1].value as number) : current
    const delta = current - prev
    return {
      ...d,
      _deltaBase: i > 0 ? Math.min(prev, current) : current,
      _deltaSize: i > 0 ? Math.abs(delta) : 0,
      _delta: delta,
    }
  }) as (Record<string, string | number> & { _deltaBase: number; _deltaSize: number; _delta: number })[]

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
  const compositionOptions = meta?.type === 'income_expenses' || meta?.type === 'cash_flow'
    ? ['summary', 'byIncome', 'byExpenses'] as const
    : ['summary', 'detailed'] as const

  // Build donut data based on composition view
  const composition = data?.composition ?? []

  const nwDetailAccounts = composition.filter((c: ReportCompositionItem) => c.group === 'accounts').sort((a, b) => b.value - a.value)
  const nwDetailAssets = composition.filter((c: ReportCompositionItem) => c.group === 'assets').sort((a, b) => b.value - a.value)
  const nwDetailLiabs = composition.filter((c: ReportCompositionItem) => c.group === 'liabilities').sort((a, b) => b.value - a.value)
  const nwDetailTotalAccounts = nwDetailAccounts.reduce((s: number, c: ReportCompositionItem) => s + c.value, 0)
  const nwDetailTotalAssets = nwDetailAssets.reduce((s: number, c: ReportCompositionItem) => s + c.value, 0)
  const nwDetailTotalLiabs = nwDetailLiabs.reduce((s: number, c: ReportCompositionItem) => s + c.value, 0)
  const nwBarsAccounts = nwDetailAccounts.slice(0, COMPOSITION_TOP_N)
  const nwBarsAssets = nwDetailAssets.slice(0, COMPOSITION_TOP_N)
  const nwBarsLiabs = nwDetailLiabs.slice(0, COMPOSITION_TOP_N)

  const accountsGradient = buildGroupGradient(colorMap['accounts'] || '#6366F1', COMPOSITION_TOP_N)
  const assetsGradient = buildGroupGradient(colorMap['assets'] || '#F59E0B', COMPOSITION_TOP_N)
  const liabsGradient = buildGroupGradient(colorMap['liabilities'] || '#F43F5E', COMPOSITION_TOP_N)

  const lastNWIndex = netWorthSummaryData.length - 1
  const isCurrentNW = selectedNWBar == null || selectedNWBar.index === lastNWIndex
  const nwPeriodLabel = isCurrentNW ? t('reports.current') : selectedNWBar!.date

  const selectedAccounts = selectedNWBar ? selectedNWBar.accounts : nwDetailTotalAccounts
  const selectedAssets = selectedNWBar ? selectedNWBar.assets : nwDetailTotalAssets
  const selectedLiabs = selectedNWBar ? selectedNWBar.liabilities : nwDetailTotalLiabs

  type NWPieItem = { name: string; value: number; color: string }

  // Inner ring: group-level totals for Accounts + Assets chart
  const nwPieAInner: NWPieItem[] = [
    { name: t('reports.accounts'), value: selectedAccounts, color: colorMap['accounts'] || '#6366F1' },
    { name: t('reports.assets'), value: selectedAssets, color: colorMap['assets'] || '#F59E0B' },
  ].filter((d) => d.value > 0).sort((a, b) => b.value - a.value)

  // Outer ring: items grouped to match inner ring order, sorted by value within each group
  const nwPieAOuter: NWPieItem[] = nwPieAInner.flatMap((group) => {
    const isAccounts = group.name === t('reports.accounts')
    const items = isAccounts ? nwDetailAccounts : nwDetailAssets
    const gradient = isAccounts ? accountsGradient : assetsGradient
    const total = isAccounts ? nwDetailTotalAccounts : nwDetailTotalAssets
    const selected = isAccounts ? selectedAccounts : selectedAssets
    const sorted = items
      .map((item: ReportCompositionItem) => ({
        name: item.label,
        value: total > 0 ? (item.value / total) * selected : 0,
      }))
      .filter((d) => d.value > 0)
      .sort((a, b) => b.value - a.value)
      .map((d, i) => ({ ...d, color: gradient[i] ?? gradient[gradient.length - 1] ?? '#6B7280' }))
    const top = sorted.slice(0, COMPOSITION_TOP_N)
    const otherValue = sorted.slice(COMPOSITION_TOP_N).reduce((s, d) => s + d.value, 0)
    if (otherValue > 0) top.push({ name: t('reports.other'), value: otherValue, color: gradient[gradient.length - 1] ?? '#6B7280' })
    return top
  })

  // Inner ring: liabilities group total
  const nwPieBInner: NWPieItem[] = [
    { name: t('reports.liabilities'), value: selectedLiabs, color: colorMap['liabilities'] || '#F43F5E' },
  ].filter((d) => d.value > 0)

  // Outer ring: individual liability items, proportionally scaled
  const nwPieBOuterSorted: NWPieItem[] = nwDetailLiabs
    .map((item: ReportCompositionItem) => ({
      name: item.label,
      value: nwDetailTotalLiabs > 0 ? (item.value / nwDetailTotalLiabs) * selectedLiabs : 0,
    }))
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value)
    .map((d, i) => ({ ...d, color: liabsGradient[i] ?? liabsGradient[liabsGradient.length - 1] ?? '#6B7280' }))
  const nwPieBOuter: NWPieItem[] = (() => {
    const top = nwPieBOuterSorted.slice(0, COMPOSITION_TOP_N)
    const otherValue = nwPieBOuterSorted.slice(COMPOSITION_TOP_N).reduce((s, d) => s + d.value, 0)
    if (otherValue > 0) top.push({ name: t('reports.other'), value: otherValue, color: liabsGradient[liabsGradient.length - 1] ?? '#6B7280' })
    return top
  })()

  type NWBarState = { accounts: number; assets: number; liabilities: number; value: number; date: string; index: number } | null

  const handleSummaryBarClick = (_data: unknown, index: number) => {
    const row = netWorthSummaryData[index]
    if (!row) return
    setSelectedNWBar((prev: NWBarState) =>
      prev?.index === index ? null : {
        accounts: (row.accounts as number) ?? 0,
        assets: (row.assets as number) ?? 0,
        liabilities: Math.abs((row.liabilitiesNeg as number) ?? 0),
        value: (row.value as number) ?? 0,
        date: row.date as string,
        index,
      }
    )
  }

  const handleDetailedBarClick = (_data: unknown, index: number) => {
    const row = netWorthDetailedData[index]
    if (!row) return
    setSelectedNWBar((prev: NWBarState) => {
      if (prev?.index === index) return null
      const accounts = nwBarsAccounts.reduce((s: number, it: ReportCompositionItem) => s + ((row[`acct_${it.key}`] as number) ?? 0), 0) + ((row['acct_others'] as number) ?? 0)
      const assets = nwBarsAssets.reduce((s: number, it: ReportCompositionItem) => s + ((row[`asset_${it.key}`] as number) ?? 0), 0) + ((row['asset_others'] as number) ?? 0)
      const liabilities = Math.abs(nwBarsLiabs.reduce((s: number, it: ReportCompositionItem) => s + ((row[`liab_${it.key}`] as number) ?? 0), 0) + ((row['liab_others'] as number) ?? 0))
      return { accounts, assets, liabilities, value: (row.value as number) ?? 0, date: row.date as string, index }
    })
  }

  const netWorthDetailedData: Record<string, string | number>[] = meta?.type !== 'net_worth' ? [] : chartData.map((d: Record<string, string | number>) => {
    const aTotal = (d.accounts as number) ?? 0
    const sTotal = (d.assets as number) ?? 0
    const lTotal = (d.liabilities as number) ?? 0
    const row: Record<string, string | number> = { date: d.date as string, value: d.value as number }
    nwBarsAccounts.forEach((item) => {
      const p = nwDetailTotalAccounts > 0 ? item.value / nwDetailTotalAccounts : 0
      row[`acct_${item.key}`] = Math.round(aTotal * p * 100) / 100
    })
    if (nwDetailAccounts.length > COMPOSITION_TOP_N) {
      const topSum = nwBarsAccounts.reduce((s, item) => s + Math.round(aTotal * (nwDetailTotalAccounts > 0 ? item.value / nwDetailTotalAccounts : 0) * 100) / 100, 0)
      row['acct_others'] = Math.round((aTotal - topSum) * 100) / 100
    }
    nwBarsAssets.forEach((item) => {
      const p = nwDetailTotalAssets > 0 ? item.value / nwDetailTotalAssets : 0
      row[`asset_${item.key}`] = Math.round(sTotal * p * 100) / 100
    })
    if (nwDetailAssets.length > COMPOSITION_TOP_N) {
      const topSum = nwBarsAssets.reduce((s, item) => s + Math.round(sTotal * (nwDetailTotalAssets > 0 ? item.value / nwDetailTotalAssets : 0) * 100) / 100, 0)
      row['asset_others'] = Math.round((sTotal - topSum) * 100) / 100
    }
    nwBarsLiabs.forEach((item) => {
      const p = nwDetailTotalLiabs > 0 ? item.value / nwDetailTotalLiabs : 0
      row[`liab_${item.key}`] = -Math.round(lTotal * p * 100) / 100
    })
    if (nwDetailLiabs.length > COMPOSITION_TOP_N) {
      const topSum = nwBarsLiabs.reduce((s, item) => s + Math.round(lTotal * (nwDetailTotalLiabs > 0 ? item.value / nwDetailTotalLiabs : 0) * 100) / 100, 0)
      row['liab_others'] = -Math.round((lTotal - topSum) * 100) / 100
    }
    return row
  })

  const donutData = (() => {
    if (compositionView === 'summary' || composition.length === 0) {
      const excludedKeys = new Set(['netIncome', 'startingBalance', 'endingBalance'])
      return breakdownData
        .filter((b) => b.value > 0 && !excludedKeys.has(b.key))
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
              {rangeOptions.map((opt) => (
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
                <>
                  <div className="flex items-center gap-1.5">
                    <div className="w-3 h-0 border-t-2" style={{ borderColor: '#6366F1' }} />
                    <span className="text-[11px] text-muted-foreground">{t('reports.netWorth')}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <div className="w-2.5 h-2.5 rounded-sm overflow-hidden flex">
                      <div className="flex-1" style={{ backgroundColor: '#10B981', opacity: 0.8 }} />
                      <div className="flex-1" style={{ backgroundColor: '#F43F5E', opacity: 0.8 }} />
                    </div>
                    <span className="text-[11px] text-muted-foreground">{t('reports.change')}</span>
                  </div>
                </>
              ) : (
                <>
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
                </>
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
                    <Area
                      type="monotone"
                      dataKey="value"
                      stroke="#6366F1"
                      strokeWidth={2.5}
                      fill="url(#cashFlowGrad)"
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
              <ComposedChart data={nwTrendData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="netWorthGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366F1" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#6366F1" stopOpacity={0.01} />
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
                    const point = payload[0]?.payload as (typeof nwTrendData)[0] | undefined
                    if (!point) return null
                    const nw = point.value as number
                    const delta = point._delta as number
                    return (
                      <div style={tooltipStyle} className="px-3 py-2">
                        <p className="text-xs font-medium mb-1">{label}</p>
                        <p className="text-xs" style={{ color: '#6366F1' }}>
                          {t('reports.netWorth')}: {privacyMode ? MASK : formatCurrency(nw, userCurrency, locale)}
                        </p>
                        {delta !== 0 && (
                          <p className="text-xs" style={{ color: delta >= 0 ? '#10B981' : '#F43F5E' }}>
                            {t('reports.change')}: {privacyMode ? MASK : `${delta >= 0 ? '+' : ''}${formatCurrency(delta, userCurrency, locale)}`}
                          </p>
                        )}
                      </div>
                    )
                  }}
                />
                {/* Invisible base lifts delta bars to float from prev → current value */}
                <Bar dataKey="_deltaBase" stackId="nwdelta" fillOpacity={0} stroke="none" maxBarSize={14} isAnimationActive={false} legendType="none" />
                <Bar dataKey="_deltaSize" stackId="nwdelta" maxBarSize={14} radius={[2, 2, 2, 2]} isAnimationActive={false} legendType="none">
                  {nwTrendData.map((entry, i) => (
                    <Cell
                      key={i}
                      fill={(entry._delta as number) >= 0 ? '#10B981' : '#F43F5E'}
                      fillOpacity={(entry._delta as number) === 0 ? 0 : 0.75}
                    />
                  ))}
                </Bar>
                <Area
                  type="monotone"
                  dataKey="value"
                  stroke="#6366F1"
                  strokeWidth={2.5}
                  fill="url(#netWorthGrad)"
                  dot={false}
                  activeDot={{ r: 4, fill: '#6366F1' }}
                />
              </ComposedChart>
            </ResponsiveContainer>
            )
          ) : (
            <p className="text-muted-foreground text-sm text-center py-16">
              {t('reports.noData')}
            </p>
          )}
        </div>
      </div>

      {meta?.type === 'net_worth' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {/* Composition card — two nested pie charts, synced to selected bar */}
          <div className="bg-card rounded-xl border border-border shadow-sm">
            <div className="px-5 pt-4 pb-3 flex items-start justify-between">
              <div>
                <p className="text-sm font-semibold text-foreground">{t('reports.composition')}</p>
                <span className="text-xs text-muted-foreground">{nwPeriodLabel}</span>
              </div>
              <div className="text-right">
                <p className="text-[10px] text-muted-foreground leading-tight">{t('reports.netWorth')}</p>
                <p className="text-sm font-bold text-foreground tabular-nums">
                  {mask(formatCompact(selectedNWBar ? selectedNWBar.value : (summary?.primary_value ?? 0), userCurrency, locale))}
                </p>
              </div>
            </div>
            <div className="pb-4">
              {isLoading ? (
                <div className="px-4" style={{ height: 180 }}>
                  <Skeleton className="h-full w-full" />
                </div>
              ) : (
                <div className="flex flex-row items-start justify-around px-1 gap-1">
                  {/* Pie A: Accounts + Assets */}
                  <div className="relative flex flex-col items-center gap-1 hover:z-10">
                    <p className="text-[11px] font-medium text-muted-foreground">{t('reports.accountsAndAssets')}</p>
                    {nwPieAInner.length > 0 ? (
                      <div className="relative" style={{ width: 148, height: 148 }}>
                        <div
                          className="absolute inset-0"
                          style={{
                            transform: compositionView === 'detailed' ? 'scale(1)' : 'scale(1.458)',
                            transition: 'transform 0.5s cubic-bezier(0.4, 0, 0.2, 1)',
                            transformOrigin: 'center',
                            zIndex: 1,
                          }}
                        >
                          <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                              <Pie
                                data={compositionView === 'detailed' ? nwPieAOuter : []}
                                innerRadius={52}
                                outerRadius={70}
                                paddingAngle={0}
                                dataKey="value"
                                stroke="var(--card)"
                                strokeWidth={1}
                                animationBegin={0}
                                animationDuration={500}
                              >
                                {nwPieAOuter.map((entry, idx) => (
                                  <Cell key={idx} fill={entry.color} />
                                ))}
                              </Pie>
                              <Pie
                                data={nwPieAInner}
                                innerRadius={28}
                                outerRadius={48}
                                paddingAngle={0}
                                dataKey="value"
                                stroke="var(--card)"
                                strokeWidth={1}
                                animationBegin={50}
                                animationDuration={500}
                              >
                                {nwPieAInner.map((entry, idx) => (
                                  <Cell key={idx} fill={entry.color} />
                                ))}
                              </Pie>
                              <Tooltip
                                formatter={(value?: number, name?: string) => {
                                  const v = value ?? 0
                                  const total = selectedAccounts + selectedAssets
                                  const pct = total > 0 ? ((v / total) * 100).toFixed(1) : '0'
                                  return [privacyMode ? MASK : `${formatCurrency(v, userCurrency, locale)} (${pct}%)`, name]
                                }}
                                contentStyle={{ ...tooltipStyle, zIndex: 10 }}
                                itemStyle={tooltipItemStyle}
                                wrapperStyle={{ zIndex: 10 }}
                              />
                            </PieChart>
                          </ResponsiveContainer>
                        </div>
                        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                          <span className="text-[10px] font-bold text-foreground tabular-nums">
                            {mask(formatCompact(selectedAccounts + selectedAssets, userCurrency, locale))}
                          </span>
                        </div>
                      </div>
                    ) : (
                      <p className="text-muted-foreground text-xs text-center py-10">{t('reports.noData')}</p>
                    )}
                  </div>

                  {/* Pie B: Liabilities */}
                  <div className="relative flex flex-col items-center gap-1 hover:z-10">
                    <p className="text-[11px] font-medium text-muted-foreground">{t('reports.liabilities')}</p>
                    {nwPieBInner.length > 0 ? (
                      <div className="relative" style={{ width: 148, height: 148 }}>
                        <div
                          className="absolute inset-0"
                          style={{
                            transform: compositionView === 'detailed' ? 'scale(1)' : 'scale(1.458)',
                            transition: 'transform 0.5s cubic-bezier(0.4, 0, 0.2, 1)',
                            transformOrigin: 'center',
                            zIndex: 1,
                          }}
                        >
                          <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                              <Pie
                                data={compositionView === 'detailed' ? nwPieBOuter : []}
                                innerRadius={52}
                                outerRadius={70}
                                paddingAngle={0}
                                dataKey="value"
                                stroke="var(--card)"
                                strokeWidth={1}
                                animationBegin={0}
                                animationDuration={500}
                              >
                                {nwPieBOuter.map((entry, idx) => (
                                  <Cell key={idx} fill={entry.color} />
                                ))}
                              </Pie>
                              <Pie
                                data={nwPieBInner}
                                innerRadius={28}
                                outerRadius={48}
                                paddingAngle={0}
                                dataKey="value"
                                stroke="var(--card)"
                                strokeWidth={1}
                                animationBegin={50}
                                animationDuration={500}
                              >
                                {nwPieBInner.map((entry, idx) => (
                                  <Cell key={idx} fill={entry.color} />
                                ))}
                              </Pie>
                              <Tooltip
                                formatter={(value?: number, name?: string) => {
                                  const v = value ?? 0
                                  const pct = selectedLiabs > 0 ? ((v / selectedLiabs) * 100).toFixed(1) : '0'
                                  return [privacyMode ? MASK : `${formatCurrency(v, userCurrency, locale)} (${pct}%)`, name]
                                }}
                                contentStyle={{ ...tooltipStyle, zIndex: 10 }}
                                itemStyle={tooltipItemStyle}
                                wrapperStyle={{ zIndex: 10 }}
                              />
                            </PieChart>
                          </ResponsiveContainer>
                        </div>
                        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                          <span className="text-[10px] font-bold text-foreground tabular-nums">
                            {mask(formatCompact(selectedLiabs, userCurrency, locale))}
                          </span>
                        </div>
                      </div>
                    ) : (
                      <p className="text-muted-foreground text-xs text-center py-10">{t('reports.noData')}</p>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Evolution chart */}
          <div className="lg:col-span-2 bg-card rounded-xl border border-border shadow-sm">
            <div className="px-5 pt-4 pb-2 flex items-center justify-between">
              <div className="flex items-center gap-4">
                <p className="text-sm font-semibold text-foreground">{t('reports.evolution')}</p>
                {compositionView === 'summary' && (
                  <div className="hidden sm:flex items-center gap-3">
                    {[
                      { key: 'accounts', color: colorMap['accounts'] || '#6366F1' },
                      { key: 'assets', color: colorMap['assets'] || '#F59E0B' },
                      { key: 'liabilities', color: colorMap['liabilities'] || '#F43F5E' },
                    ].map(({ key, color }) => (
                      <div key={key} className="flex items-center gap-1.5">
                        <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
                        <span className="text-[11px] text-muted-foreground">{t(`reports.${key}`)}</span>
                      </div>
                    ))}
                    <div className="flex items-center gap-1.5">
                      <div className="w-3 h-0 border-t-2 border-dashed" style={{ borderColor: '#10B981' }} />
                      <span className="text-[11px] text-muted-foreground">{t('reports.netWorth')}</span>
                    </div>
                  </div>
                )}
              </div>
              <div className="flex items-center rounded-lg border border-border bg-muted/30 overflow-hidden">
                {(['summary', 'detailed'] as const).map((opt) => (
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
            {compositionView === 'summary' ? (
              <div className="px-1 pb-4" style={{ height: 320 }}>
                {isLoading ? (
                  <div className="px-4"><Skeleton className="h-full w-full" /></div>
                ) : netWorthSummaryData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart
                      data={netWorthSummaryData}
                      stackOffset="sign"
                      margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
                      style={{ cursor: 'pointer' }}
                    >
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
                          const liabEntry = payload.find((p) => p.dataKey === 'liabilitiesNeg')
                          const netEntry = payload.find((p) => p.dataKey === 'value')
                          return (
                            <div style={tooltipStyle} className="px-3 py-2">
                              <p className="text-xs font-medium mb-1">{label}</p>
                              {payload
                                .filter((p) => p.dataKey !== 'liabilitiesNeg' && p.dataKey !== 'value' && (p.value as number) !== 0)
                                .sort((a, b) => (b.value as number) - (a.value as number))
                                .map((p) => (
                                  <p key={p.dataKey as string} className="text-xs" style={{ color: p.color }}>
                                    {t(`reports.${p.dataKey}`, { defaultValue: String(p.name) })}:{' '}
                                    {privacyMode ? MASK : formatCurrency(p.value as number, userCurrency, locale)}
                                  </p>
                                ))
                              }
                              {liabEntry && (liabEntry.value as number) !== 0 && (
                                <p className="text-xs" style={{ color: '#F43F5E' }}>
                                  {t('reports.liabilities')}:{' '}
                                  {privacyMode ? MASK : formatCurrency(-(liabEntry.value as number), userCurrency, locale)}
                                </p>
                              )}
                              {netEntry && (
                                <p className="text-xs font-semibold mt-1" style={{ color: '#10B981' }}>
                                  {t('reports.netWorth')}:{' '}
                                  {privacyMode ? MASK : formatCurrency(netEntry.value as number, userCurrency, locale)}
                                </p>
                              )}
                            </div>
                          )
                        }}
                      />
                      <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="3 3" />
                      <Bar dataKey="accounts" stackId="stack" fill={colorMap['accounts'] || '#6366F1'} maxBarSize={32} radius={[0, 0, 0, 0]} onClick={handleSummaryBarClick}>
                        {netWorthSummaryData.map((_: unknown, i: number) => (
                          <Cell key={i} fill={colorMap['accounts'] || '#6366F1'} opacity={selectedNWBar == null || selectedNWBar.index === i ? 1 : 0.35} />
                        ))}
                      </Bar>
                      <Bar dataKey="assets" stackId="stack" fill={colorMap['assets'] || '#F59E0B'} maxBarSize={32} radius={[4, 4, 0, 0]} onClick={handleSummaryBarClick}>
                        {netWorthSummaryData.map((_: unknown, i: number) => (
                          <Cell key={i} fill={colorMap['assets'] || '#F59E0B'} opacity={selectedNWBar == null || selectedNWBar.index === i ? 1 : 0.35} />
                        ))}
                      </Bar>
                      <Bar dataKey="liabilitiesNeg" stackId="stack" fill={colorMap['liabilities'] || '#F43F5E'} maxBarSize={32} radius={[4, 4, 0, 0]} onClick={handleSummaryBarClick}>
                        {netWorthSummaryData.map((_: unknown, i: number) => (
                          <Cell key={i} fill={colorMap['liabilities'] || '#F43F5E'} opacity={selectedNWBar == null || selectedNWBar.index === i ? 1 : 0.35} />
                        ))}
                      </Bar>
                      <Line
                        type="monotone"
                        dataKey="value"
                        stroke="#10B981"
                        strokeWidth={2}
                        strokeDasharray="6 3"
                        dot={false}
                        activeDot={{ r: 4, fill: '#10B981' }}
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="text-muted-foreground text-sm text-center py-16">{t('reports.noData')}</p>
                )}
              </div>
            ) : (
              <div className="px-1 pb-4" style={{ height: 320 }}>
                {isLoading ? (
                  <div className="px-4"><Skeleton className="h-full w-full" /></div>
                ) : netWorthDetailedData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart
                      data={netWorthDetailedData}
                      stackOffset="sign"
                      margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
                      style={{ cursor: 'pointer' }}
                    >
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
                        content={({ active, payload, label }: { active?: boolean; payload?: readonly { dataKey?: string | number; value?: number; color?: string }[]; label?: string | number }) => {
                          if (!active || !payload) return null
                          const findLabel = (dk: string) => {
                            if (dk.endsWith('_others')) return t('reports.other')
                            const label = composition.find((c: ReportCompositionItem) => c.key === dk.replace(/^(acct_|asset_|liab_)/, ''))?.label ?? dk
                            return label.length > 40 ? label.slice(0, 40) + '…' : label
                          }
                          const acctEntries = payload.filter((p) => String(p.dataKey).startsWith('acct_') && (p.value ?? 0) !== 0).sort((a, b) => Math.abs(b.value ?? 0) - Math.abs(a.value ?? 0))
                          const assetEntries = payload.filter((p) => String(p.dataKey).startsWith('asset_') && (p.value ?? 0) !== 0).sort((a, b) => Math.abs(b.value ?? 0) - Math.abs(a.value ?? 0))
                          const liabEntries = payload.filter((p) => String(p.dataKey).startsWith('liab_') && (p.value ?? 0) !== 0).sort((a, b) => Math.abs(b.value ?? 0) - Math.abs(a.value ?? 0))
                          const netEntry = payload.find((p) => p.dataKey === 'value')
                          return (
                            <div style={tooltipStyle} className="px-3 py-2">
                              <p className="text-xs font-medium mb-1">{label}</p>
                              {acctEntries.length > 0 && <p className="text-[10px] font-semibold text-muted-foreground mt-1 uppercase tracking-wide">{t('reports.accounts')}</p>}
                              {acctEntries.map((p) => (
                                <div key={String(p.dataKey)} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 2 }}>
                                  <span className="text-xs flex items-center gap-1.5" style={{ color: p.color }}>
                                    <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: p.color, flexShrink: 0, display: 'inline-block' }} />
                                    {findLabel(String(p.dataKey))}
                                  </span>
                                  <span className="text-xs" style={{ fontVariantNumeric: 'tabular-nums', color: p.color }}>{privacyMode ? MASK : formatCurrency(p.value ?? 0, userCurrency, locale)}</span>
                                </div>
                              ))}
                              {assetEntries.length > 0 && <p className="text-[10px] font-semibold text-muted-foreground mt-1 uppercase tracking-wide">{t('reports.assets')}</p>}
                              {assetEntries.map((p) => (
                                <div key={String(p.dataKey)} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 2 }}>
                                  <span className="text-xs flex items-center gap-1.5" style={{ color: p.color }}>
                                    <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: p.color, flexShrink: 0, display: 'inline-block' }} />
                                    {findLabel(String(p.dataKey))}
                                  </span>
                                  <span className="text-xs" style={{ fontVariantNumeric: 'tabular-nums', color: p.color }}>{privacyMode ? MASK : formatCurrency(p.value ?? 0, userCurrency, locale)}</span>
                                </div>
                              ))}
                              {liabEntries.length > 0 && <p className="text-[10px] font-semibold text-muted-foreground mt-1 uppercase tracking-wide">{t('reports.liabilities')}</p>}
                              {liabEntries.map((p) => (
                                <div key={String(p.dataKey)} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 2 }}>
                                  <span className="text-xs flex items-center gap-1.5" style={{ color: p.color }}>
                                    <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: p.color, flexShrink: 0, display: 'inline-block' }} />
                                    {findLabel(String(p.dataKey))}
                                  </span>
                                  <span className="text-xs" style={{ fontVariantNumeric: 'tabular-nums', color: p.color }}>{privacyMode ? MASK : formatCurrency(-(p.value ?? 0), userCurrency, locale)}</span>
                                </div>
                              ))}
                              {netEntry && (
                                <div style={{ borderTop: '1px solid var(--border)', marginTop: 6, paddingTop: 6, display: 'flex', justifyContent: 'space-between', gap: 16 }}>
                                  <span className="text-xs font-semibold" style={{ color: '#10B981' }}>{t('reports.netWorth')}</span>
                                  <span className="text-xs font-semibold" style={{ fontVariantNumeric: 'tabular-nums', color: '#10B981' }}>{privacyMode ? MASK : formatCurrency(netEntry.value ?? 0, userCurrency, locale)}</span>
                                </div>
                              )}
                            </div>
                          )
                        }}
                      />
                      <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="3 3" />
                      {nwBarsAccounts.map((item: ReportCompositionItem, idx: number) => {
                        const color = accountsGradient[idx] ?? accountsGradient[accountsGradient.length - 1]
                        return (
                          <Bar key={`acct_${item.key}`} dataKey={`acct_${item.key}`} stackId="stack" fill={color} maxBarSize={32} radius={[0, 0, 0, 0]} onClick={handleDetailedBarClick}>
                            {netWorthDetailedData.map((_: unknown, i: number) => (
                              <Cell key={i} fill={color} opacity={selectedNWBar == null || selectedNWBar.index === i ? 1 : 0.35} />
                            ))}
                          </Bar>
                        )
                      })}
                      {nwDetailAccounts.length > COMPOSITION_TOP_N && (() => {
                        const color = accountsGradient[accountsGradient.length - 1] ?? '#6B7280'
                        return (
                          <Bar key="acct_others" dataKey="acct_others" stackId="stack" fill={color} maxBarSize={32} radius={nwBarsAssets.length === 0 && nwDetailLiabs.length === 0 ? [4, 4, 0, 0] : [0, 0, 0, 0]} onClick={handleDetailedBarClick}>
                            {netWorthDetailedData.map((_: unknown, i: number) => (
                              <Cell key={i} fill={color} opacity={selectedNWBar == null || selectedNWBar.index === i ? 1 : 0.35} />
                            ))}
                          </Bar>
                        )
                      })()}
                      {nwBarsAssets.map((item: ReportCompositionItem, idx: number) => {
                        const color = assetsGradient[idx] ?? assetsGradient[assetsGradient.length - 1]
                        return (
                          <Bar key={`asset_${item.key}`} dataKey={`asset_${item.key}`} stackId="stack" fill={color} maxBarSize={32} radius={[0, 0, 0, 0]} onClick={handleDetailedBarClick}>
                            {netWorthDetailedData.map((_: unknown, i: number) => (
                              <Cell key={i} fill={color} opacity={selectedNWBar == null || selectedNWBar.index === i ? 1 : 0.35} />
                            ))}
                          </Bar>
                        )
                      })}
                      {nwDetailAssets.length > COMPOSITION_TOP_N && (() => {
                        const color = assetsGradient[assetsGradient.length - 1] ?? '#6B7280'
                        return (
                          <Bar key="asset_others" dataKey="asset_others" stackId="stack" fill={color} maxBarSize={32} radius={nwDetailLiabs.length === 0 ? [4, 4, 0, 0] : [0, 0, 0, 0]} onClick={handleDetailedBarClick}>
                            {netWorthDetailedData.map((_: unknown, i: number) => (
                              <Cell key={i} fill={color} opacity={selectedNWBar == null || selectedNWBar.index === i ? 1 : 0.35} />
                            ))}
                          </Bar>
                        )
                      })()}
                      {nwBarsLiabs.map((item: ReportCompositionItem, idx: number) => {
                        const color = liabsGradient[idx] ?? liabsGradient[liabsGradient.length - 1]
                        return (
                          <Bar key={`liab_${item.key}`} dataKey={`liab_${item.key}`} stackId="stack" fill={color} maxBarSize={32} radius={[0, 0, 0, 0]} onClick={handleDetailedBarClick}>
                            {netWorthDetailedData.map((_: unknown, i: number) => (
                              <Cell key={i} fill={color} opacity={selectedNWBar == null || selectedNWBar.index === i ? 1 : 0.35} />
                            ))}
                          </Bar>
                        )
                      })}
                      {nwDetailLiabs.length > COMPOSITION_TOP_N && (() => {
                        const color = liabsGradient[liabsGradient.length - 1] ?? '#6B7280'
                        return (
                          <Bar key="liab_others" dataKey="liab_others" stackId="stack" fill={color} maxBarSize={32} radius={[4, 4, 0, 0]} onClick={handleDetailedBarClick}>
                            {netWorthDetailedData.map((_: unknown, i: number) => (
                              <Cell key={i} fill={color} opacity={selectedNWBar == null || selectedNWBar.index === i ? 1 : 0.35} />
                            ))}
                          </Bar>
                        )
                      })()}
                      <Line
                        type="monotone"
                        dataKey="value"
                        stroke="#10B981"
                        strokeWidth={2}
                        strokeDasharray="6 3"
                        dot={false}
                        activeDot={{ r: 4, fill: '#10B981' }}
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="text-muted-foreground text-sm text-center py-16">{t('reports.noData')}</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}
      {!isLoading && meta?.type !== 'net_worth' && (
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
                      : meta?.type === 'cash_flow'
                        ? t('reports.vsToday')
                        : t(currentTab.labelKey)
                const centerValue = compositionView === 'byIncome'
                  ? (summary?.breakdowns.find((b) => b.key === 'income' || b.key === 'projectedIncome')?.value ?? 0)
                  : compositionView === 'byExpenses'
                    ? (summary?.breakdowns.find((b) => b.key === 'expenses' || b.key === 'projectedExpenses')?.value ?? 0)
                    : meta?.type === 'cash_flow'
                      ? (summary?.change_amount ?? 0)
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
                  {(() => {
                    const barSeries = meta.type === 'cash_flow'
                      ? [
                          { key: 'inflow', color: '#10B981' },
                          { key: 'outflow', color: '#F43F5E' },
                        ]
                      : meta.series_keys.map((k) => ({ key: k, color: colorMap[k] || '#6366F1' }))
                    return barSeries
                      .filter(({ key }) => chartData.some((d) => (d[key] as number) > 0))
                      .map(({ key, color }, idx, arr) => (
                        <Bar
                          key={key}
                          dataKey={key}
                          stackId="stack"
                          fill={color}
                          radius={idx === arr.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
                          maxBarSize={32}
                        />
                      ))
                  })()}
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
      )}
    </div>
  )
}
