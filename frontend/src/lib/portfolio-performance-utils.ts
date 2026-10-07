import type { BenchmarkMatch, PortfolioPerformancePeriod, UserPreferences } from '../types'

const STORAGE_PREFIX = 'securo.performanceBenchmark.'
export const MAX_PERFORMANCE_BENCHMARKS = 5

export function benchmarkStorageKey(workspaceId: string): string {
  return STORAGE_PREFIX + workspaceId
}

function isBenchmarkMatch(value: unknown): value is BenchmarkMatch {
  if (!value || typeof value !== 'object') return false
  const benchmark = value as Partial<BenchmarkMatch>
  return (
    typeof benchmark.symbol === 'string' &&
    typeof benchmark.name === 'string' &&
    (benchmark.provider === 'yahoo' || benchmark.provider === 'b3') &&
    (benchmark.exchange === null || typeof benchmark.exchange === 'string')
  )
}

export function readBenchmarkSelections(workspaceId: string): BenchmarkMatch[] {
  if (!workspaceId) return []
  try {
    const raw = localStorage.getItem(benchmarkStorageKey(workspaceId))
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    // Migrate the original one-object format without changing the storage key.
    const values = Array.isArray(parsed) ? parsed : [parsed]
    const selections: BenchmarkMatch[] = []
    const seen = new Set<string>()
    for (const value of values) {
      if (!isBenchmarkMatch(value)) continue
      const key = `${value.provider}:${value.symbol}`
      if (seen.has(key)) continue
      seen.add(key)
      selections.push(value)
      if (selections.length === MAX_PERFORMANCE_BENCHMARKS) break
    }
    return selections
  } catch {
    return []
  }
}

export function writeBenchmarkSelections(
  workspaceId: string,
  benchmarks: BenchmarkMatch[],
): void {
  if (!workspaceId) return
  const key = benchmarkStorageKey(workspaceId)
  if (benchmarks.length) {
    localStorage.setItem(
      key,
      JSON.stringify(benchmarks.slice(0, MAX_PERFORMANCE_BENCHMARKS)),
    )
  }
  else localStorage.removeItem(key)
}

// Compatibility helpers for callers/tests written for the original feature.
export function readBenchmarkSelection(workspaceId: string): BenchmarkMatch | null {
  return readBenchmarkSelections(workspaceId)[0] ?? null
}

export function writeBenchmarkSelection(
  workspaceId: string,
  benchmark: BenchmarkMatch | null,
): void {
  writeBenchmarkSelections(workspaceId, benchmark ? [benchmark] : [])
}

const SELECTION_PREFIX = 'securo.performanceSelection.'
const PERIODS: PortfolioPerformancePeriod[] = ['3m', '6m', 'ytd', '1y', '3y', '5y']

/** What the performance chart is showing; restored when the tab reopens. */
export interface PerformanceSelection {
  scope: 'all' | 'custom'
  walletIds: string[]
  assetIds: string[]
  period: PortfolioPerformancePeriod
}

/** A named set of wallets and holdings, saved to the user's preferences. */
export interface PerformanceView {
  id: string
  name: string
  walletIds: string[]
  assetIds: string[]
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

export function readLastSelection(workspaceId: string): PerformanceSelection | null {
  if (!workspaceId) return null
  try {
    const raw = localStorage.getItem(SELECTION_PREFIX + workspaceId)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PerformanceSelection>
    return {
      scope: parsed.scope === 'custom' ? 'custom' : 'all',
      walletIds: stringList(parsed.walletIds),
      assetIds: stringList(parsed.assetIds),
      period: PERIODS.includes(parsed.period as PortfolioPerformancePeriod)
        ? (parsed.period as PortfolioPerformancePeriod)
        : '1y',
    }
  } catch {
    return null
  }
}

export function writeLastSelection(workspaceId: string, selection: PerformanceSelection): void {
  if (!workspaceId) return
  try {
    localStorage.setItem(SELECTION_PREFIX + workspaceId, JSON.stringify(selection))
  } catch {
    // Storage unavailable (private mode): the selection just isn't remembered.
  }
}

export function readSavedViews(
  preferences: UserPreferences | null | undefined,
  workspaceId: string,
): PerformanceView[] {
  const views: unknown = preferences?.performance_views?.[workspaceId]
  if (!Array.isArray(views)) return []
  return views.flatMap((value: unknown) => {
    if (!value || typeof value !== 'object') return []
    const view = value as Record<string, unknown>
    if (typeof view.id !== 'string' || typeof view.name !== 'string') return []
    return [
      {
        id: view.id,
        name: view.name,
        walletIds: stringList(view.wallet_ids),
        assetIds: stringList(view.asset_ids),
      },
    ]
  })
}

/** Preferences with this workspace's saved views replaced, others untouched. */
export function withSavedViews(
  preferences: UserPreferences | null | undefined,
  workspaceId: string,
  views: PerformanceView[],
): UserPreferences {
  return {
    ...(preferences ?? {}),
    performance_views: {
      ...(preferences?.performance_views ?? {}),
      [workspaceId]: views.map((view) => ({
        id: view.id,
        name: view.name,
        wallet_ids: view.walletIds,
        asset_ids: view.assetIds,
      })),
    },
  }
}

export function isSameSelection(
  view: Pick<PerformanceView, 'walletIds' | 'assetIds'>,
  walletIds: string[],
  assetIds: string[],
): boolean {
  const same = (a: string[], b: string[]) =>
    a.length === b.length && [...a].sort().every((id, index) => id === [...b].sort()[index])
  return same(view.walletIds, walletIds) && same(view.assetIds, assetIds)
}

/**
 * Return between two points of a cumulative return series, in percent.
 * Cumulative returns compound, so the change from A to B is the ratio of
 * their growth factors, not the difference of the percentages.
 */
export function periodReturn(startPercent: number, endPercent: number): number | null {
  const startGrowth = 1 + startPercent / 100
  if (!(startGrowth > 0)) return null
  return ((1 + endPercent / 100) / startGrowth - 1) * 100
}
