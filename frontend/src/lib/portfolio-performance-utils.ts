import type {
  BenchmarkMatch,
  PerformanceMethod,
  PortfolioPerformancePeriod,
  UserPreferences,
} from '../types'

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
  method: PerformanceMethod
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
      method: parsed.method === 'mwr' ? 'mwr' : 'twr',
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

/**
 * A random id for a new saved view. `crypto.randomUUID` only exists in
 * secure contexts, and self-hosted installs are often opened over plain
 * http on a LAN address; `getRandomValues` works on both.
 */
export function newViewId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
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

const DAY_MS = 86_400_000
const MAX_LOG_GROWTH = 45
const CASH_EPSILON = 1e-9

/**
 * Money-weighted return (IRR) between two points, as a cumulative percentage.
 * Mirrors the server's `money_weighted_return`: what was held at `start`,
 * plus the money put in after it (`flows`, negative when taken out), grows
 * into what is held at `end` at a single rate, compounded over the time
 * money was actually invested. Unlike a time-weighted series, the return
 * of a range can't be derived from the cumulative values, so it is solved
 * again from the values and flows.
 */
export function moneyWeightedReturn(
  dates: string[],
  values: number[],
  flows: number[],
  start: number,
  end: number,
): number | null {
  const time = (index: number) => new Date(`${dates[index]}T00:00:00Z`).getTime() / DAY_MS
  const cash: [number, number][] = [[time(start), -Math.max(values[start] ?? 0, 0)]]
  for (let index = start + 1; index <= end; index += 1) cash.push([time(index), -(flows[index] ?? 0)])
  cash.push([time(end), Math.max(values[end] ?? 0, 0)])
  const moved = cash.filter(([, amount]) => Math.abs(amount) > CASH_EPSILON)
  if (!moved.length) return 0
  const paid = moved.reduce((sum, [, amount]) => (amount < 0 ? sum - amount : sum), 0)
  const received = moved.reduce((sum, [, amount]) => (amount > 0 ? sum + amount : sum), 0)
  if (paid <= 0) return null
  if (received <= 0) return -100
  const first = Math.min(...moved.map(([when]) => when))
  const span = Math.max(...moved.map(([when]) => when)) - first
  if (span === 0) return (received / paid - 1) * 100
  const timed = moved.map(([when, amount]) => [(when - first) / span, amount] as const)
  const npv = (x: number) => {
    let total = 0
    let slope = 0
    for (const [elapsed, amount] of timed) {
      const term = amount * Math.exp(-x * elapsed)
      total += term
      slope -= elapsed * term
    }
    return [total, slope] as const
  }
  let low = -MAX_LOG_GROWTH
  let high = MAX_LOG_GROWTH
  const [lowValue] = npv(low)
  const [highValue] = npv(high)
  if (lowValue === 0) return (Math.exp(low) - 1) * 100
  if (lowValue > 0 === highValue > 0) return null
  const lowPositive = lowValue > 0
  const tolerance = 1e-12 * (paid + received)
  let x = 0
  for (let iteration = 0; iteration < 200; iteration += 1) {
    const [value, slope] = npv(x)
    if (Math.abs(value) <= tolerance) break
    if (value > 0 === lowPositive) low = x
    else high = x
    // Newton's step, falling back to bisection when it leaves the bracket.
    const step = slope ? x - value / slope : Number.NaN
    const next = step > low && step < high ? step : (low + high) / 2
    if (Math.abs(next - x) < 1e-13) {
      x = next
      break
    }
    x = next
  }
  return (Math.exp(x) - 1) * 100
}
