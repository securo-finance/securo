import { useEffect, useId, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  Activity,
  Bookmark,
  ChartNoAxesCombined,
  ChevronDown,
  Info,
  Loader2,
  Plus,
  Search,
  Trash2,
  Wallet,
  X,
} from 'lucide-react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { assets, auth as authApi } from '@/lib/api'
import { useAuth } from '@/contexts/auth-context'
import { useFeatureFlags } from '@/hooks/use-feature-flags'
import {
  MAX_PERFORMANCE_BENCHMARKS,
  type PerformanceView,
  isSameSelection,
  periodReturn,
  readBenchmarkSelections,
  readLastSelection,
  readSavedViews,
  withSavedViews,
  writeBenchmarkSelections,
  writeLastSelection,
} from '@/lib/portfolio-performance-utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { Asset, AssetGroup, BenchmarkMatch, PortfolioPerformancePeriod } from '@/types'

const PERIODS: PortfolioPerformancePeriod[] = ['3m', '6m', 'ytd', '1y', '3y', '5y']
const PERIOD_LABELS: Record<PortfolioPerformancePeriod, string> = {
  '3m': '3M',
  '6m': '6M',
  ytd: 'YTD',
  '1y': '1Y',
  '3y': '3Y',
  '5y': '5Y',
}
const BENCHMARK_COLORS = ['#6366F1', '#F59E0B', '#06B6D4', '#8B5CF6', '#F43F5E']
const BENCHMARK_DASHES = ['6 3', '2 3', '8 3 2 3', '10 4', '4 4']

function benchmarkKey(benchmark: Pick<BenchmarkMatch, 'provider' | 'symbol'>): string {
  return `${benchmark.provider}:${benchmark.symbol}`
}

function apiErrorDetail(error: unknown): string | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' && detail.trim() ? detail : null
}

function returnColor(value: number | null | undefined): string {
  if (value == null || value === 0) return 'text-foreground'
  return value > 0 ? 'text-emerald-700 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'
}

export function PortfolioPerformance({
  workspaceId,
  assetGroupIds,
  holdings,
  wallets,
  locale,
  dateLocale,
  mask,
}: {
  workspaceId: string
  assetGroupIds: string[] | null
  holdings: Asset[]
  wallets: AssetGroup[]
  locale: string
  dateLocale: string
  mask: (value: string) => string
}) {
  const { t } = useTranslation()
  const chartId = useId().replace(/:/g, '')
  const [benchmarkOpen, setBenchmarkOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const { benchmarksEnabled } = useFeatureFlags()
  const [storedBenchmarks, setSelected] = useState<BenchmarkMatch[]>(() =>
    readBenchmarkSelections(workspaceId),
  )
  // With benchmarks disabled on the server, nothing is compared or fetched.
  const selected = useMemo(
    () => (benchmarksEnabled ? storedBenchmarks : []),
    [benchmarksEnabled, storedBenchmarks],
  )
  // Reopen on whatever was selected last time in this workspace.
  const [lastSelection] = useState(() => readLastSelection(workspaceId))
  const [period, setPeriod] = useState<PortfolioPerformancePeriod>(lastSelection?.period ?? '1y')
  const [scope, setScope] = useState<'all' | 'custom'>(lastSelection?.scope ?? 'all')
  const [scopeOpen, setScopeOpen] = useState(false)
  const [selectedWalletIds, setSelectedWalletIds] = useState<string[]>(
    lastSelection?.walletIds ?? [],
  )
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>(lastSelection?.assetIds ?? [])
  const { user, updateUser } = useAuth()
  const savedViews = useMemo(
    () => readSavedViews(user?.preferences, workspaceId),
    [user?.preferences, workspaceId],
  )
  // The saved view last applied, so edits to it can be saved back.
  const [editingViewId, setEditingViewId] = useState<string | null>(null)
  const [viewName, setViewName] = useState('')
  const [savingView, setSavingView] = useState(false)

  useEffect(() => {
    writeLastSelection(workspaceId, {
      scope,
      walletIds: selectedWalletIds,
      assetIds: selectedAssetIds,
      period,
    })
  }, [workspaceId, scope, selectedWalletIds, selectedAssetIds, period])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 400)
    return () => window.clearTimeout(timer)
  }, [query])

  const selectedKeys = useMemo(() => new Set(selected.map(benchmarkKey)), [selected])
  const searchEnabled =
    benchmarkOpen && debouncedQuery.length >= 2 && selected.length < MAX_PERFORMANCE_BENCHMARKS
  const search = useQuery({
    queryKey: ['benchmark-search', debouncedQuery],
    queryFn: () => assets.benchmarkSearch(debouncedQuery),
    enabled: searchEnabled,
    staleTime: 15 * 60 * 1000,
    gcTime: 60 * 60 * 1000,
    retry: 1,
  })
  const searchMatches = useMemo(
    () => (search.data ?? []).filter((match) => !selectedKeys.has(benchmarkKey(match))),
    [search.data, selectedKeys],
  )

  const sortedGroupIds = useMemo(
    () => (assetGroupIds ? [...assetGroupIds].sort() : null),
    [assetGroupIds],
  )
  const requestWalletIds = useMemo(
    () => (scope === 'custom' ? [...selectedWalletIds].sort() : undefined),
    [scope, selectedWalletIds],
  )
  const requestAssetIds = useMemo(
    () => (scope === 'custom' ? [...selectedAssetIds].sort() : undefined),
    [scope, selectedAssetIds],
  )
  const emptyCollection = sortedGroupIds !== null && sortedGroupIds.length === 0
  const emptyCustomScope =
    scope === 'custom' && !requestWalletIds?.length && !requestAssetIds?.length
  const performance = useQuery({
    queryKey: [
      'asset-performance',
      workspaceId,
      selected.map(benchmarkKey),
      period,
      sortedGroupIds,
      requestWalletIds,
      requestAssetIds,
    ],
    queryFn: () =>
      assets.performance(selected, period, sortedGroupIds, requestWalletIds, requestAssetIds),
    enabled: !emptyCollection && !emptyCustomScope,
    // A failed benchmark source is transient: retry it on the next visit
    // instead of serving the cached error for the usual five minutes.
    staleTime: (query) =>
      query.state.data?.benchmarks.some((metric) => metric.source_error) ? 0 : 5 * 60 * 1000,
    retry: 1,
  })

  const holdingsByGroup = useMemo(() => {
    const grouped = new Map<string | null, Asset[]>()
    for (const holding of holdings) {
      const groupId = holding.group_id ?? null
      const groupHoldings = grouped.get(groupId) ?? []
      groupHoldings.push(holding)
      grouped.set(groupId, groupHoldings)
    }
    return grouped
  }, [holdings])
  const ungroupedHoldings = holdingsByGroup.get(null) ?? []

  const percentFormatter = useMemo(
    () =>
      new Intl.NumberFormat(locale, {
        style: 'percent',
        maximumFractionDigits: 2,
        minimumFractionDigits: 2,
        signDisplay: 'exceptZero',
      }),
    [locale],
  )
  const pointsFormatter = useMemo(
    () =>
      new Intl.NumberFormat(locale, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
        signDisplay: 'exceptZero',
      }),
    [locale],
  )
  // Axis ticks keep only the decimals they need, so a chart that moves less
  // than 1% still gets distinct labels (0.25%, 0.5%…).
  const axisFormatter = useMemo(
    () => new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 2 }),
    [locale],
  )
  const formatExcess = (value: number | null | undefined) =>
    value == null
      ? '—'
      : mask(t('assets.performancePercentagePoints', { value: pointsFormatter.format(value) }))
  const formatDate = (value: string) =>
    new Date(`${value}T00:00:00`).toLocaleDateString(dateLocale, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    })
  const formatReturn = (value: number | null | undefined) =>
    value == null ? '—' : mask(percentFormatter.format(value / 100))

  const chooseBenchmark = (benchmark: BenchmarkMatch) => {
    if (selectedKeys.has(benchmarkKey(benchmark))) return
    const next = [...selected, benchmark].slice(0, MAX_PERFORMANCE_BENCHMARKS)
    setSelected(next)
    writeBenchmarkSelections(workspaceId, next)
    setQuery('')
    setDebouncedQuery('')
  }

  const removeBenchmark = (key: string) => {
    const next = selected.filter((benchmark) => benchmarkKey(benchmark) !== key)
    setSelected(next)
    writeBenchmarkSelections(workspaceId, next)
  }

  const toggleWallet = (walletId: string) => {
    const isSelected = selectedWalletIds.includes(walletId)
    setSelectedWalletIds((current) =>
      isSelected ? current.filter((id) => id !== walletId) : [...current, walletId],
    )
    if (!isSelected) {
      const childIds = new Set((holdingsByGroup.get(walletId) ?? []).map((item) => item.id))
      setSelectedAssetIds((current) => current.filter((id) => !childIds.has(id)))
    }
  }

  const activeView =
    scope === 'custom'
      ? savedViews.find((view) => isSameSelection(view, selectedWalletIds, selectedAssetIds))
      : undefined
  const editingView = savedViews.find((view) => view.id === editingViewId)
  const editingViewChanged =
    !!editingView && !isSameSelection(editingView, selectedWalletIds, selectedAssetIds)

  const applyView = (view: PerformanceView) => {
    // Wallets or holdings removed since the view was saved are skipped.
    const walletIds = new Set(wallets.map((wallet) => wallet.id))
    const assetIds = new Set(holdings.map((holding) => holding.id))
    setScope('custom')
    setSelectedWalletIds(view.walletIds.filter((id) => walletIds.has(id)))
    setSelectedAssetIds(view.assetIds.filter((id) => assetIds.has(id)))
    setEditingViewId(view.id)
    setScopeOpen(false)
  }

  const storeViews = async (views: PerformanceView[]) => {
    setSavingView(true)
    try {
      const updated = await authApi.updateMe({
        preferences: withSavedViews(user?.preferences, workspaceId, views),
      })
      updateUser(updated)
      return true
    } catch {
      toast.error(t('assets.performanceViewSaveError'))
      return false
    } finally {
      setSavingView(false)
    }
  }

  const saveNewView = async () => {
    const name = viewName.trim()
    if (!name) return
    const view: PerformanceView = {
      id: crypto.randomUUID(),
      name,
      walletIds: [...selectedWalletIds],
      assetIds: [...selectedAssetIds],
    }
    if (await storeViews([...savedViews, view])) {
      setEditingViewId(view.id)
      setViewName('')
      toast.success(t('assets.performanceViewSaved', { name }))
    }
  }

  const updateEditingView = async () => {
    if (!editingView) return
    const next = savedViews.map((view) =>
      view.id === editingView.id
        ? { ...view, walletIds: [...selectedWalletIds], assetIds: [...selectedAssetIds] }
        : view,
    )
    if (await storeViews(next)) {
      toast.success(t('assets.performanceViewSaved', { name: editingView.name }))
    }
  }

  const deleteView = async (view: PerformanceView) => {
    if (await storeViews(savedViews.filter((item) => item.id !== view.id))) {
      if (editingViewId === view.id) setEditingViewId(null)
      toast.success(t('assets.performanceViewDeleted', { name: view.name }))
    }
  }

  const toggleAsset = (assetId: string) => {
    setSelectedAssetIds((current) =>
      current.includes(assetId) ? current.filter((id) => id !== assetId) : [...current, assetId],
    )
  }

  const data = performance.data
  const hasData = !!data && data.points.length >= 2 && data.portfolio_return != null
  const availableBenchmarks = selected
    .map((benchmark, index) => ({ benchmark, index }))
    .filter(({ benchmark }) =>
      data?.benchmarks.some(
        (metric) =>
          metric.key === benchmarkKey(benchmark) &&
          !metric.source_error &&
          metric.benchmark_return != null,
      ),
    )
  const sourceErrorMessages = {
    unavailable: t('assets.performanceSourceError'),
    rate_limited: t('assets.performanceSourceRateLimited'),
    no_data: t('assets.performanceSourceNoData'),
  }
  const chartData = useMemo(
    () =>
      (data?.points ?? []).map((point) => {
        const row: Record<string, string | number | null> = {
          date: point.date,
          portfolio: point.portfolio,
        }
        selected.forEach((benchmark, index) => {
          row[`benchmark_${index}`] = point.benchmarks?.[benchmarkKey(benchmark)] ?? null
        })
        return row
      }),
    [data?.points, selected],
  )

  // Drag across the chart to measure the return between two dates. The
  // selection belongs to the data it was made on, so a refetch drops it.
  const [range, setRange] = useState<{
    start: string
    end: string
    source: typeof chartData
  } | null>(null)
  const [dragging, setDragging] = useState(false)
  const indexByDate = useMemo(
    () => new Map(chartData.map((row, index) => [String(row.date), index])),
    [chartData],
  )
  const rangeStats = useMemo(() => {
    if (!range || range.source !== chartData) return null
    const startIndex = indexByDate.get(range.start)
    const endIndex = indexByDate.get(range.end)
    if (startIndex === undefined || endIndex === undefined || startIndex === endIndex) return null
    const from = chartData[Math.min(startIndex, endIndex)]
    const to = chartData[Math.max(startIndex, endIndex)]
    const between = (key: string) =>
      from[key] == null || to[key] == null ? null : periodReturn(Number(from[key]), Number(to[key]))
    return {
      startDate: String(from.date),
      endDate: String(to.date),
      portfolio: between('portfolio'),
      benchmarks: selected.map((_, index) => between(`benchmark_${index}`)),
    }
  }, [range, chartData, indexByDate, selected])
  // Within about six months, month labels alone repeat; show the day too.
  const shortSpan =
    chartData.length > 1 &&
    new Date(String(chartData[chartData.length - 1].date)).getTime() -
      new Date(String(chartData[0].date)).getTime() <=
      186 * 86_400_000
  const chartLabel = (state: { activeLabel?: unknown } | null | undefined) =>
    state?.activeLabel == null ? null : String(state.activeLabel)

  const scopeLabel =
    scope === 'all'
      ? t('assets.performanceAllAssets')
      : (activeView?.name ?? t('assets.performanceCustomAssets'))
  const selectionCount = selectedWalletIds.length + selectedAssetIds.length
  const showComparisonSidebar = selected.length > 0 && selected.length <= 3

  return (
    <section
      className="min-w-0 overflow-hidden rounded-xl border border-border bg-card shadow-sm"
      aria-labelledby={`${chartId}-title`}
    >
      <div className="flex flex-col gap-5 p-4 sm:p-6 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-start gap-3">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Activity size={20} aria-hidden="true" />
          </div>
          <div>
            <h2
              id={`${chartId}-title`}
              className="text-lg font-semibold tracking-tight text-foreground"
            >
              {t('assets.performanceTitle')}
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {t('assets.performanceDescription')}
            </p>
          </div>
        </div>
        <div
          role="group"
          aria-label={t('assets.performancePeriod')}
          className="grid shrink-0 grid-cols-6 gap-1 rounded-lg bg-muted/60 p-1"
        >
          {PERIODS.map((item) => (
            <button
              key={item}
              type="button"
              aria-pressed={period === item}
              onClick={() => setPeriod(item)}
              className={`min-h-9 rounded-md px-2 text-xs font-semibold transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring sm:px-3 ${
                period === item
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:bg-card/60 hover:text-foreground'
              }`}
            >
              {PERIOD_LABELS[item]}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-3 border-y border-border bg-muted/20 px-4 py-3 sm:px-6">
        <div className="min-w-0 flex-1 basis-48 sm:flex-none sm:basis-auto">
          <p id={`${chartId}-scope`} className="mb-1.5 text-xs font-medium text-muted-foreground">
            {t('assets.performanceScope')}
          </p>
          <Popover open={scopeOpen} onOpenChange={setScopeOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                className="h-10 w-full justify-start bg-card sm:w-auto sm:max-w-80"
                aria-describedby={`${chartId}-scope`}
              >
                <Wallet size={16} className="text-muted-foreground" aria-hidden="true" />
                <span className="truncate">{scopeLabel}</span>
                {scope === 'custom' && (
                  <span className="rounded-md bg-muted px-1.5 py-0.5 text-xs tabular-nums text-muted-foreground">
                    {selectionCount}
                  </span>
                )}
                <ChevronDown
                  size={14}
                  className="ml-auto text-muted-foreground"
                  aria-hidden="true"
                />
              </Button>
            </PopoverTrigger>
            <PopoverContent
              align="start"
              className="max-h-[var(--radix-popover-content-available-height)] w-[min(26rem,calc(100vw-2rem))] overflow-y-auto p-0"
              aria-label={t('assets.performanceScope')}
            >
              <div className="border-b border-border p-4">
                <p className="mb-3 text-sm font-semibold">{t('assets.performanceScope')}</p>
                <div className="flex flex-wrap gap-2">
                  <ScopeChip
                    active={scope === 'all'}
                    onClick={() => {
                      setScope('all')
                      setScopeOpen(false)
                    }}
                  >
                    {t('assets.performanceAllAssets')}
                  </ScopeChip>
                  {savedViews.map((view) => (
                    <ScopeChip
                      key={view.id}
                      active={activeView?.id === view.id}
                      onClick={() => applyView(view)}
                    >
                      <Bookmark size={13} className="shrink-0" aria-hidden="true" />
                      <span className="truncate">{view.name}</span>
                    </ScopeChip>
                  ))}
                  <ScopeChip
                    active={scope === 'custom' && !activeView}
                    onClick={() => {
                      if (scope !== 'custom') setEditingViewId(null)
                      setScope('custom')
                    }}
                  >
                    {t('assets.performanceCustomAssets')}
                  </ScopeChip>
                </div>
              </div>
              {scope === 'custom' && (
                <div className="max-h-[min(24rem,45dvh)] space-y-3 overflow-y-auto overscroll-contain p-3">
                  {wallets.map((wallet) => {
                    const walletSelected = selectedWalletIds.includes(wallet.id)
                    const walletHoldings = holdingsByGroup.get(wallet.id) ?? []
                    return (
                      <div key={wallet.id} className="space-y-1">
                        <label className="flex min-h-10 cursor-pointer items-center gap-2 rounded-md px-2 text-sm font-medium hover:bg-muted/60">
                          <input
                            type="checkbox"
                            checked={walletSelected}
                            onChange={() => toggleWallet(wallet.id)}
                            className="h-4 w-4 shrink-0 rounded border-border accent-primary"
                          />
                          <span
                            className="h-2.5 w-2.5 shrink-0 rounded-full"
                            style={{ backgroundColor: wallet.color }}
                          />
                          <span className="min-w-0 truncate">{wallet.name}</span>
                        </label>
                        {walletHoldings.map((holding) => (
                          <label
                            key={holding.id}
                            className={`ml-6 flex min-h-9 items-center gap-2 rounded-md px-2 text-sm ${
                              walletSelected
                                ? 'cursor-not-allowed text-muted-foreground/60'
                                : 'cursor-pointer text-muted-foreground'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={walletSelected || selectedAssetIds.includes(holding.id)}
                              disabled={walletSelected}
                              onChange={() => toggleAsset(holding.id)}
                              className="h-4 w-4 shrink-0 rounded border-border accent-primary"
                            />
                            <span className="truncate">
                              {holding.name}
                              {holding.ticker ? ` · ${holding.ticker}` : ''}
                            </span>
                          </label>
                        ))}
                      </div>
                    )
                  })}
                  {ungroupedHoldings.length > 0 && (
                    <div className="space-y-1">
                      <p className="text-xs font-semibold text-muted-foreground">
                        {t('assets.ungrouped')}
                      </p>
                      {ungroupedHoldings.map((holding) => (
                        <label
                          key={holding.id}
                          className="flex min-h-9 cursor-pointer items-center gap-2 rounded-md px-2 text-sm text-muted-foreground hover:bg-muted/60"
                        >
                          <input
                            type="checkbox"
                            checked={selectedAssetIds.includes(holding.id)}
                            onChange={() => toggleAsset(holding.id)}
                            className="h-4 w-4 shrink-0 rounded border-border accent-primary"
                          />
                          <span className="truncate">
                            {holding.name}
                            {holding.ticker ? ` · ${holding.ticker}` : ''}
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                  <div className="space-y-2 border-t border-border pt-3">
                    {editingView && (
                      <div className="flex flex-wrap items-center gap-2">
                        {editingViewChanged && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-auto min-h-9 max-w-full whitespace-normal"
                            disabled={savingView}
                            onClick={updateEditingView}
                          >
                            {t('assets.performanceViewUpdate', { name: editingView.name })}
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={savingView}
                          onClick={() => deleteView(editingView)}
                          className="h-auto min-h-9 max-w-full whitespace-normal text-rose-600 hover:text-rose-700"
                        >
                          <Trash2 size={14} />
                          {t('assets.performanceViewDelete', { name: editingView.name })}
                        </Button>
                      </div>
                    )}
                    {!activeView &&
                      (selectedWalletIds.length > 0 || selectedAssetIds.length > 0) && (
                        <form
                          className="flex flex-wrap gap-2"
                          onSubmit={(event) => {
                            event.preventDefault()
                            void saveNewView()
                          }}
                        >
                          <Input
                            value={viewName}
                            onChange={(event) => setViewName(event.target.value)}
                            placeholder={t('assets.performanceViewNamePlaceholder')}
                            aria-label={t('assets.performanceViewNamePlaceholder')}
                            maxLength={40}
                            className="min-w-0 text-sm"
                          />
                          <Button type="submit" size="sm" disabled={savingView || !viewName.trim()}>
                            <Bookmark size={14} />
                            {t('assets.performanceViewSave')}
                          </Button>
                        </form>
                      )}
                  </div>
                </div>
              )}
              <div className="flex items-center justify-between gap-3 border-t border-border p-3">
                <p className="text-xs text-muted-foreground" aria-live="polite">
                  {scope === 'custom'
                    ? t('assets.performanceSelectionCount', { count: selectionCount })
                    : t('assets.performanceAllAssets')}
                </p>
                <Button size="sm" variant="secondary" onClick={() => setScopeOpen(false)}>
                  {t('common.close')}
                </Button>
              </div>
            </PopoverContent>
          </Popover>
        </div>

        {benchmarksEnabled && (
          <Popover
            open={benchmarkOpen}
            onOpenChange={(open) => {
              setBenchmarkOpen(open)
              if (!open) {
                setQuery('')
                setDebouncedQuery('')
              }
            }}
          >
            <PopoverTrigger asChild>
              <Button variant="outline" className="h-10 bg-card">
                <Plus size={16} aria-hidden="true" />
                {t('assets.performanceCompare')}
                {selected.length > 0 && (
                  <span className="rounded-md bg-primary/10 px-1.5 py-0.5 text-xs tabular-nums text-primary">
                    {selected.length}
                  </span>
                )}
              </Button>
            </PopoverTrigger>
            <PopoverContent
              align="start"
              className="max-h-[var(--radix-popover-content-available-height)] w-[min(24rem,calc(100vw-2rem))] overflow-y-auto p-0"
              aria-label={t('assets.performanceBenchmarks')}
            >
              <div className="space-y-3 p-4">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold">{t('assets.performanceBenchmarks')}</p>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {selected.length} / {MAX_PERFORMANCE_BENCHMARKS}
                  </span>
                </div>
                {selected.length < MAX_PERFORMANCE_BENCHMARKS ? (
                  <>
                    <div className="relative">
                      <Search
                        size={16}
                        className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <Input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder={t('assets.benchmarkSearchPlaceholder')}
                        aria-label={t('assets.benchmarkSearchPlaceholder')}
                        className="h-10 pl-9 pr-10"
                      />
                      {query && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => setQuery('')}
                          className="absolute right-1 top-1/2 -translate-y-1/2"
                          aria-label={t('common.close')}
                        >
                          <X size={15} aria-hidden="true" />
                        </Button>
                      )}
                    </div>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {t('assets.benchmarkSearchHint')}
                    </p>
                  </>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    {t('assets.performanceBenchmarkLimit')}
                  </p>
                )}
                {selected.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {selected.map((benchmark, index) => (
                      <div
                        key={benchmarkKey(benchmark)}
                        className="inline-flex max-w-full items-center gap-2 rounded-lg border border-border py-0.5 pl-2.5 pr-0.5"
                      >
                        <span
                          className="size-2 shrink-0 rounded-full"
                          style={{ backgroundColor: BENCHMARK_COLORS[index] }}
                        />
                        <span className="truncate text-xs font-medium" title={benchmark.name}>
                          {benchmark.name}
                        </span>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => removeBenchmark(benchmarkKey(benchmark))}
                          aria-label={t('assets.performanceRemoveBenchmark', {
                            name: benchmark.name,
                          })}
                        >
                          <X size={14} aria-hidden="true" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {query.trim().length >= 2 && selected.length < MAX_PERFORMANCE_BENCHMARKS && (
                <div
                  className="border-t border-border"
                  aria-live="polite"
                  aria-busy={search.isFetching || query.trim() !== debouncedQuery}
                >
                  {search.isFetching || query.trim() !== debouncedQuery ? (
                    <div className="space-y-2 p-3">
                      <Skeleton className="h-10" />
                      <Skeleton className="h-10" />
                    </div>
                  ) : search.isError ? (
                    <p className="p-4 text-sm text-rose-600 dark:text-rose-400">
                      {apiErrorDetail(search.error) ?? t('assets.performanceLoadError')}
                    </p>
                  ) : searchMatches.length > 0 ? (
                    <div className="max-h-[min(16rem,35dvh)] overflow-y-auto overscroll-contain p-1">
                      {searchMatches.map((match) => (
                        <button
                          key={benchmarkKey(match)}
                          type="button"
                          onClick={() => chooseBenchmark(match)}
                          className="flex min-h-12 w-full items-center gap-3 rounded-md px-3 py-2.5 text-left outline-none hover:bg-muted/60 focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                        >
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium">{match.name}</span>
                            <span className="block text-xs text-muted-foreground">
                              {match.symbol}
                              {match.exchange ? ` · ${match.exchange}` : ''}
                            </span>
                          </span>
                          <Plus
                            size={16}
                            className="shrink-0 text-muted-foreground"
                            aria-hidden="true"
                          />
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="p-4 text-sm text-muted-foreground">
                      {t('assets.benchmarkNoResults')}
                    </p>
                  )}
                </div>
              )}
            </PopoverContent>
          </Popover>
        )}
        {performance.isFetching &&
          !performance.isLoading &&
          !emptyCollection &&
          !emptyCustomScope && (
            <Loader2
              size={16}
              className="my-3 ml-auto animate-spin text-muted-foreground motion-reduce:animate-none"
              aria-label={t('common.loading')}
            />
          )}
      </div>

      <div
        className="p-4 sm:p-6"
        aria-busy={performance.isFetching && !emptyCollection && !emptyCustomScope}
      >
        {emptyCollection ? (
          <PerformanceEmptyState message={t('assets.performanceNoPortfolio')} />
        ) : emptyCustomScope ? (
          <PerformanceEmptyState message={t('assets.performanceNoSelection')}>
            <Button variant="outline" onClick={() => setScopeOpen(true)}>
              {t('assets.performanceSelectAssets')}
            </Button>
          </PerformanceEmptyState>
        ) : performance.isLoading ? (
          <div className="space-y-6" role="status" aria-label={t('common.loading')}>
            <div className="space-y-3">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-12 w-52" />
              <Skeleton className="h-4 w-64 max-w-full" />
            </div>
            <Skeleton className="h-72 rounded-xl sm:h-80" />
          </div>
        ) : performance.isError ? (
          <PerformanceEmptyState
            message={apiErrorDetail(performance.error) ?? t('assets.performanceLoadError')}
            error
          >
            <Button variant="outline" onClick={() => performance.refetch()}>
              {t('common.retry')}
            </Button>
          </PerformanceEmptyState>
        ) : !hasData ? (
          <PerformanceEmptyState message={t('assets.performanceNoData')} />
        ) : (
          <>
            <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                  <span className="h-0.5 w-4 rounded-full bg-emerald-500" />
                  {rangeStats ? t('assets.performanceSelectedPeriod') : t('assets.portfolioReturn')}
                </p>
                <p
                  className={`mt-1.5 text-2xl font-semibold tracking-tight tabular-nums sm:text-3xl ${returnColor(rangeStats ? rangeStats.portfolio : data.portfolio_return)}`}
                  aria-live="polite"
                >
                  {formatReturn(rangeStats ? rangeStats.portfolio : data.portfolio_return)}
                </p>
                <p className="mt-1.5 text-xs text-muted-foreground">
                  <time dateTime={rangeStats?.startDate ?? data.start_date}>
                    {formatDate(rangeStats?.startDate ?? data.start_date)}
                  </time>
                  {' – '}
                  <time dateTime={rangeStats?.endDate ?? data.end_date}>
                    {formatDate(rangeStats?.endDate ?? data.end_date)}
                  </time>
                </p>
              </div>
              {rangeStats && (
                <Button variant="ghost" size="sm" onClick={() => setRange(null)}>
                  <X size={14} aria-hidden="true" />
                  {t('assets.performanceClearSelection')}
                </Button>
              )}
            </div>

            <div
              className={`grid min-w-0 gap-6 ${showComparisonSidebar ? 'xl:grid-cols-[minmax(0,1fr)_17rem]' : ''}`}
            >
              <div className="min-w-0">
                <div className="h-72 w-full cursor-crosshair select-none rounded-lg has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring sm:h-80 [&_*:focus]:outline-none">
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart
                      data={chartData}
                      margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
                      accessibilityLayer
                      onMouseDown={(state) => {
                        const label = chartLabel(state)
                        if (!label) return
                        setDragging(true)
                        setRange({ start: label, end: label, source: chartData })
                      }}
                      onMouseMove={(state) => {
                        const label = chartLabel(state)
                        if (!dragging || !label) return
                        setRange((current) => (current ? { ...current, end: label } : current))
                      }}
                      onMouseUp={() => {
                        setDragging(false)
                        // A click without dragging clears the selection.
                        setRange((current) =>
                          current && current.start === current.end ? null : current,
                        )
                      }}
                      onMouseLeave={() => setDragging(false)}
                    >
                      <defs>
                        <linearGradient id={`${chartId}-fill`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#10B981" stopOpacity={0.16} />
                          <stop offset="100%" stopColor="#10B981" stopOpacity={0.01} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid
                        strokeDasharray="3 5"
                        vertical={false}
                        stroke="var(--border)"
                      />
                      <ReferenceLine
                        y={0}
                        stroke="var(--muted-foreground)"
                        strokeDasharray="4 4"
                        strokeOpacity={0.5}
                      />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                        axisLine={false}
                        tickLine={false}
                        tickMargin={12}
                        minTickGap={40}
                        tickFormatter={(value: string) =>
                          new Date(`${value}T00:00:00`).toLocaleDateString(
                            dateLocale,
                            shortSpan
                              ? { day: 'numeric', month: 'short' }
                              : { month: 'short', year: '2-digit' },
                          )
                        }
                      />
                      <YAxis
                        tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                        axisLine={false}
                        tickLine={false}
                        width={58}
                        tickMargin={8}
                        tickFormatter={(value: number) => mask(axisFormatter.format(value / 100))}
                      />
                      {rangeStats && (
                        <ReferenceArea
                          x1={rangeStats.startDate}
                          x2={rangeStats.endDate}
                          fill="var(--primary)"
                          fillOpacity={0.08}
                          stroke="var(--primary)"
                          strokeOpacity={0.35}
                          strokeDasharray="3 3"
                          ifOverflow="hidden"
                        />
                      )}
                      <RechartsTooltip
                        active={dragging ? false : undefined}
                        cursor={{
                          stroke: 'var(--muted-foreground)',
                          strokeDasharray: '3 3',
                          strokeOpacity: 0.5,
                        }}
                        content={({ active, payload, label }) => {
                          if (!active || !payload?.length || !label) return null
                          const row = payload[0].payload as Record<string, string | number | null>
                          // A date with no value (e.g. outside an index's history)
                          // shows a dash, matching the gap in its line.
                          const returnAt = (key: string) => (row[key] == null ? null : Number(row[key]))
                          return (
                            <div className="max-w-[min(20rem,75vw)] rounded-xl border border-border bg-popover p-3 text-xs shadow-lg">
                              <p className="mb-3 border-b border-border pb-2 font-medium text-muted-foreground">
                                {formatDate(String(label))}
                              </p>
                              <div className="flex items-center justify-between gap-4">
                                <span className="min-w-0 truncate">
                                  {t('assets.portfolioReturn')}
                                </span>
                                <span className="shrink-0 font-semibold tabular-nums">
                                  {formatReturn(returnAt('portfolio'))}
                                </span>
                              </div>
                              {availableBenchmarks.map(({ benchmark, index }) => (
                                <div
                                  key={benchmarkKey(benchmark)}
                                  className="mt-2 flex items-center gap-2"
                                >
                                  <span
                                    className="size-2 shrink-0 rounded-full"
                                    style={{ backgroundColor: BENCHMARK_COLORS[index] }}
                                  />
                                  <span className="min-w-0 flex-1 truncate">{benchmark.name}</span>
                                  <span className="shrink-0 font-medium tabular-nums">
                                    {formatReturn(returnAt(`benchmark_${index}`))}
                                  </span>
                                </div>
                              ))}
                            </div>
                          )
                        }}
                      />
                      <Area
                        type="monotone"
                        dataKey="portfolio"
                        name={t('assets.portfolioReturn')}
                        stroke="#10B981"
                        strokeWidth={2.5}
                        fill={`url(#${chartId}-fill)`}
                        baseValue={0}
                        dot={false}
                        activeDot={{ r: 4, stroke: 'var(--card)', strokeWidth: 2 }}
                        isAnimationActive={false}
                      />
                      {availableBenchmarks.map(({ benchmark, index }) => (
                        <Line
                          key={benchmarkKey(benchmark)}
                          type="monotone"
                          dataKey={`benchmark_${index}`}
                          name={benchmark.name}
                          stroke={BENCHMARK_COLORS[index]}
                          strokeDasharray={BENCHMARK_DASHES[index]}
                          strokeWidth={1.75}
                          dot={false}
                          activeDot={{ r: 4, stroke: 'var(--card)', strokeWidth: 2 }}
                          isAnimationActive={false}
                        />
                      ))}
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-2">
                    <span className="h-0.5 w-4 shrink-0 rounded-full bg-emerald-500" />
                    {t('assets.portfolioReturn')}
                  </span>
                  {availableBenchmarks.map(({ benchmark, index }) => (
                    <span
                      key={benchmarkKey(benchmark)}
                      className="inline-flex min-w-0 items-center gap-2"
                    >
                      <svg width="20" height="4" className="shrink-0" aria-hidden="true">
                        <line
                          x1="0"
                          y1="2"
                          x2="20"
                          y2="2"
                          stroke={BENCHMARK_COLORS[index]}
                          strokeWidth="2"
                          strokeDasharray={BENCHMARK_DASHES[index]}
                        />
                      </svg>
                      <span className="break-words">{benchmark.name}</span>
                    </span>
                  ))}
                </div>
                <p className="mt-2 text-[11px] text-muted-foreground">
                  {t('assets.performanceDragHint')}
                </p>
              </div>

              {selected.length > 0 && (
                <aside
                  className={`min-w-0 border-t border-border pt-5 ${showComparisonSidebar ? 'xl:border-t-0 xl:border-l xl:pt-0 xl:pl-5' : ''}`}
                  aria-label={t('assets.performanceBenchmarks')}
                >
                  <h3 className="mb-3 text-sm font-semibold">
                    {t('assets.performanceBenchmarks')}
                  </h3>
                  <div
                    className={`grid gap-3 sm:grid-cols-2 ${showComparisonSidebar ? 'xl:grid-cols-1' : 'lg:grid-cols-3'}`}
                  >
                    {selected.map((benchmark, index) => {
                      const apiMetric = data.benchmarks.find(
                        (item) => item.key === benchmarkKey(benchmark),
                      )
                      const rangeBenchmark = rangeStats?.benchmarks[index] ?? null
                      const metric =
                        rangeStats && !apiMetric?.source_error
                          ? {
                              benchmark_return: rangeBenchmark,
                              excess_return:
                                rangeStats.portfolio != null && rangeBenchmark != null
                                  ? rangeStats.portfolio - rangeBenchmark
                                  : null,
                            }
                          : apiMetric
                      return (
                        <div
                          key={benchmarkKey(benchmark)}
                          className="min-w-0 rounded-lg bg-muted/40 p-3"
                        >
                          <div className="flex items-center gap-2">
                            <span
                              className="size-2 shrink-0 rounded-full"
                              style={{ backgroundColor: BENCHMARK_COLORS[index] }}
                            />
                            <p
                              className="min-w-0 flex-1 truncate text-xs font-medium"
                              title={benchmark.name}
                            >
                              {benchmark.name}
                            </p>
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              className="-my-1 -mr-1 text-muted-foreground"
                              onClick={() => removeBenchmark(benchmarkKey(benchmark))}
                              aria-label={t('assets.performanceRemoveBenchmark', {
                                name: benchmark.name,
                              })}
                            >
                              <X size={14} aria-hidden="true" />
                            </Button>
                          </div>
                          {apiMetric?.source_error ? (
                            <div className="mt-2" role="alert">
                              <p className="text-xs text-rose-600 dark:text-rose-400">
                                {sourceErrorMessages[apiMetric.source_error]}
                              </p>
                              <Button
                                variant="outline"
                                size="sm"
                                className="mt-2"
                                disabled={performance.isFetching}
                                onClick={() => performance.refetch()}
                                aria-label={t('assets.performanceRetryBenchmark', {
                                  name: benchmark.name,
                                })}
                              >
                                {t('common.retry')}
                              </Button>
                            </div>
                          ) : (
                            <>
                              <p
                                className={`mt-2 text-xl font-semibold tabular-nums ${returnColor(metric?.benchmark_return)}`}
                              >
                                {formatReturn(metric?.benchmark_return)}
                              </p>
                              <div className="mt-2 flex flex-wrap items-center justify-between gap-1 border-t border-border pt-2 text-[11px]">
                                <span className="text-muted-foreground">
                                  {t('assets.performanceExcessLabel')}
                                </span>
                                <span
                                  className={`font-medium tabular-nums ${returnColor(metric?.excess_return)}`}
                                >
                                  {formatExcess(metric?.excess_return)}
                                </span>
                              </div>
                            </>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </aside>
              )}
            </div>
          </>
        )}
      </div>
      <details className="group border-t border-border px-4 sm:px-6">
        <summary className="flex min-h-12 cursor-pointer list-none items-center gap-2 rounded-md py-3 text-xs font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
          <Info size={15} className="shrink-0" aria-hidden="true" />
          {t('assets.performanceMethodology')}
          <ChevronDown
            size={14}
            className="ml-auto shrink-0 transition-transform group-open:rotate-180"
            aria-hidden="true"
          />
        </summary>
        <p className="max-w-3xl pb-4 text-xs leading-relaxed text-muted-foreground">
          {t('assets.performanceCashFlowHint')}
        </p>
      </details>
    </section>
  )
}

function PerformanceEmptyState({
  message,
  children,
  error = false,
}: {
  message: string
  children?: ReactNode
  error?: boolean
}) {
  return (
    <div
      className="flex min-h-80 flex-col items-center justify-center gap-4 px-4 py-10 text-center"
      role={error ? 'alert' : 'status'}
    >
      <div className="flex size-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <ChartNoAxesCombined size={22} aria-hidden="true" />
      </div>
      <p
        className={`max-w-sm text-sm leading-relaxed ${error ? 'text-rose-600 dark:text-rose-400' : 'text-muted-foreground'}`}
      >
        {message}
      </p>
      {children}
    </div>
  )
}

function ScopeChip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex min-h-9 max-w-full items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring ${
        active
          ? 'border-primary/40 bg-primary/10 text-foreground'
          : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground'
      }`}
    >
      {children}
    </button>
  )
}
