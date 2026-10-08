import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { loadRange, loadReportsRange, saveRange, saveReportsRange } from './remembered-range'

// lib suites run under the node environment; give them an in-memory storage.
function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    get length() { return data.size },
    clear: () => data.clear(),
    getItem: (k) => data.get(k) ?? null,
    key: (i) => [...data.keys()][i] ?? null,
    removeItem: (k) => { data.delete(k) },
    setItem: (k, v) => { data.set(k, String(v)) },
  }
}

describe('remembered date range', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', memoryStorage())
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-03-15T12:00:00'))
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('returns null when nothing was stored', () => {
    expect(loadRange('transactions')).toBeNull()
    expect(loadReportsRange('net_worth')).toBeNull()
  })

  it('round-trips a custom range, including an open-ended one', () => {
    saveRange('transactions', '2025-01-01', '2025-06-30')
    expect(loadRange('transactions')).toEqual({ from: '2025-01-01', to: '2025-06-30' })
    saveRange('transactions', '', '')
    expect(loadRange('transactions')).toEqual({ from: '', to: '' })
  })

  it('remembers the current month as a moving target', () => {
    saveRange('transactions', '2026-03-01', '2026-03-31')
    vi.setSystemTime(new Date('2026-04-02T12:00:00'))
    expect(loadRange('transactions')).toEqual({ from: '2026-04-01', to: '2026-04-30' })
  })

  it('keeps a past month as that month', () => {
    saveRange('transactions', '2026-01-01', '2026-01-31')
    vi.setSystemTime(new Date('2026-04-02T12:00:00'))
    expect(loadRange('transactions')).toEqual({ from: '2026-01-01', to: '2026-01-31' })
  })

  it('ignores unreadable or tampered storage', () => {
    localStorage.setItem('securo.dateRange.transactions', '{not json')
    expect(loadRange('transactions')).toBeNull()
    localStorage.setItem('securo.dateRange.transactions', JSON.stringify({ kind: 'range', from: 'yesterday', to: '' }))
    expect(loadRange('transactions')).toBeNull()
  })

  it('rejects impossible calendar dates', () => {
    localStorage.setItem('securo.dateRange.transactions', JSON.stringify({ kind: 'range', from: '2026-02-30', to: '' }))
    expect(loadRange('transactions')).toBeNull()
    saveRange('transactions', '2028-02-29', '2028-03-01')
    expect(loadRange('transactions')).toEqual({ from: '2028-02-29', to: '2028-03-01' })
  })

  it('keeps a separate reports range per tab', () => {
    const netWorth = { rangeKey: '2y', from: '', to: '', interval: 'monthly' }
    const cashFlow = { rangeKey: '6m', from: '', to: '', interval: 'daily' }
    saveReportsRange('net_worth', netWorth)
    saveReportsRange('cash_flow', cashFlow)
    expect(loadReportsRange('net_worth')).toEqual(netWorth)
    expect(loadReportsRange('cash_flow')).toEqual(cashFlow)
    expect(loadReportsRange('money_map')).toBeNull()
  })
})
