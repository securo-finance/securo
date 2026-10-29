import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  benchmarkStorageKey,
  isSameSelection,
  moneyWeightedReturn,
  newViewId,
  periodReturn,
  readBenchmarkSelection,
  readBenchmarkSelections,
  readLastSelection,
  readSavedViews,
  withSavedViews,
  writeBenchmarkSelection,
  writeBenchmarkSelections,
  writeLastSelection,
} from './portfolio-performance-utils'

const values = new Map<string, string>()

beforeEach(() => {
  values.clear()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  })
})

describe('portfolio benchmark persistence', () => {
  it('is scoped by workspace', () => {
    const benchmark = { symbol: '^TEST', name: 'Test', exchange: 'SNP', provider: 'yahoo' as const }
    writeBenchmarkSelection('workspace-a', benchmark)
    expect(readBenchmarkSelection('workspace-a')).toEqual(benchmark)
    expect(readBenchmarkSelection('workspace-b')).toBeNull()
    expect(benchmarkStorageKey('workspace-a')).toBe('securo.performanceBenchmark.workspace-a')
  })

  it('ignores invalid JSON and invalid provider data', () => {
    values.set(benchmarkStorageKey('workspace-a'), '{bad json')
    expect(readBenchmarkSelection('workspace-a')).toBeNull()
    values.set(
      benchmarkStorageKey('workspace-a'),
      JSON.stringify({ symbol: '^TEST', name: 'Test', exchange: null, provider: 'unknown' }),
    )
    expect(readBenchmarkSelection('workspace-a')).toBeNull()
  })

  it('clears the saved selection', () => {
    writeBenchmarkSelection('workspace-a', {
      symbol: 'B3:DI', name: 'DI', exchange: 'B3', provider: 'b3',
    })
    writeBenchmarkSelection('workspace-a', null)
    expect(readBenchmarkSelection('workspace-a')).toBeNull()
  })

  it('persists multiple unique benchmarks and reads the legacy object format', () => {
    const first = { symbol: '^TEST', name: 'Test', exchange: 'SNP', provider: 'yahoo' as const }
    const second = { symbol: 'B3:DI', name: 'DI', exchange: 'B3', provider: 'b3' as const }
    writeBenchmarkSelections('workspace-a', [first, second])
    expect(readBenchmarkSelections('workspace-a')).toEqual([first, second])

    values.set(benchmarkStorageKey('workspace-a'), JSON.stringify(first))
    expect(readBenchmarkSelections('workspace-a')).toEqual([first])
  })

  it('ignores invalid and duplicate entries in a saved list', () => {
    const benchmark = { symbol: '^TEST', name: 'Test', exchange: null, provider: 'yahoo' as const }
    values.set(
      benchmarkStorageKey('workspace-a'),
      JSON.stringify([benchmark, { ...benchmark }, { symbol: 1, provider: 'b3' }]),
    )
    expect(readBenchmarkSelections('workspace-a')).toEqual([benchmark])
  })
})

describe('performance selection memory', () => {
  it('restores the last selection per workspace', () => {
    writeLastSelection('ws-1', {
      scope: 'custom',
      walletIds: ['w1'],
      assetIds: ['a1'],
      period: '6m',
      method: 'mwr',
    })
    expect(readLastSelection('ws-1')).toEqual({
      scope: 'custom',
      walletIds: ['w1'],
      assetIds: ['a1'],
      period: '6m',
      method: 'mwr',
    })
    expect(readLastSelection('ws-2')).toBeNull()
  })

  it('falls back to safe defaults for malformed stored data', () => {
    localStorage.setItem(
      'securo.performanceSelection.ws-1',
      JSON.stringify({ scope: 'weird', walletIds: [1, 'w1'], period: '9y' }),
    )
    expect(readLastSelection('ws-1')).toEqual({
      scope: 'all',
      walletIds: ['w1'],
      assetIds: [],
      period: '1y',
      method: 'twr',
    })
  })
})

describe('saved performance views', () => {
  const view = { id: 'v1', name: 'Retirement', walletIds: ['w1'], assetIds: ['a1'] }

  it('round-trips through preferences without touching other settings or workspaces', () => {
    const preferences = withSavedViews(
      {
        language: 'pt-BR',
        performance_views: {
          other: [{ id: 'x', name: 'Other', wallet_ids: [], asset_ids: ['z'] }],
        },
      },
      'ws-1',
      [view],
    )
    expect(preferences.language).toBe('pt-BR')
    expect(readSavedViews(preferences, 'ws-1')).toEqual([view])
    expect(readSavedViews(preferences, 'other')).toHaveLength(1)
  })

  it('creates view ids over plain http, where crypto.randomUUID is missing', () => {
    const { crypto } = globalThis
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
    try {
      const ids = new Set([newViewId(), newViewId()])
      expect(ids.size).toBe(2)
      for (const id of ids) {
        expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
      }
    } finally {
      vi.stubGlobal('crypto', crypto)
    }
  })

  it('matches a selection regardless of order', () => {
    const multi = { walletIds: ['w1', 'w2'], assetIds: ['a1'] }
    expect(isSameSelection(multi, ['w2', 'w1'], ['a1'])).toBe(true)
    expect(isSameSelection(multi, ['w1'], ['a1'])).toBe(false)
  })
})

describe('periodReturn', () => {
  it('compounds rather than subtracts cumulative returns', () => {
    // Going from +10% to -1% cumulative is a -10% move, not -11 points.
    expect(periodReturn(10, -1)).toBeCloseTo(-10, 10)
    expect(periodReturn(0, 21)).toBeCloseTo(21, 10)
    expect(periodReturn(10, 21)).toBeCloseTo(10, 10)
  })

  it('has no defined return after a total loss', () => {
    expect(periodReturn(-100, 5)).toBeNull()
  })
})

describe('moneyWeightedReturn', () => {
  const day = (offset: number) => new Date(Date.UTC(2025, 0, 1 + offset)).toISOString().slice(0, 10)

  it('is the plain change when no money moves', () => {
    expect(moneyWeightedReturn([day(0), day(365)], [1000, 1100], [0, 0], 0, 1)).toBeCloseTo(10, 6)
  })

  it('matches the server on a dip bought with new money', () => {
    // Same case as the backend: -100 - 100 g^-0.5 + 300 g^-1 = 0.
    const dates = [day(0), day(15), day(30)]
    const expected = (36 / (1 + Math.sqrt(13)) ** 2 - 1) * 100
    expect(moneyWeightedReturn(dates, [100, 150, 300], [0, 100, 0], 0, 2)).toBeCloseTo(expected, 6)
    // A range that starts after the top-up is just that range's change.
    expect(moneyWeightedReturn(dates, [100, 150, 300], [0, 100, 0], 1, 2)).toBeCloseTo(100, 6)
  })

  it('ignores the days before the first buy and after selling everything', () => {
    const dates = [day(0), day(181), day(365), day(400)]
    expect(moneyWeightedReturn(dates, [0, 1000, 1100, 1100], [0, 1000, 0, 0], 0, 2)).toBeCloseTo(
      10,
      6,
    )
    expect(moneyWeightedReturn(dates, [1000, 1100, 0, 0], [0, 0, -1100, 0], 0, 3)).toBeCloseTo(
      moneyWeightedReturn(dates, [1000, 1100, 0, 0], [0, 0, -1100, 0], 0, 2)!,
      10,
    )
  })

  it('handles nothing invested, a total loss and money that was never put in', () => {
    const dates = [day(0), day(1), day(10)]
    expect(moneyWeightedReturn(dates, [0, 0, 0], [0, 0, 0], 0, 2)).toBe(0)
    expect(moneyWeightedReturn(dates, [0, 500, 0], [0, 500, 0], 0, 2)).toBe(-100)
    expect(moneyWeightedReturn(dates, [0, 0, 0], [0, -500, 0], 0, 2)).toBeNull()
  })
})
