import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  benchmarkStorageKey,
  isSameSelection,
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
    })
    expect(readLastSelection('ws-1')).toEqual({
      scope: 'custom',
      walletIds: ['w1'],
      assetIds: ['a1'],
      period: '6m',
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
