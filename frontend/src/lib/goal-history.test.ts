import { beforeEach, describe, expect, it, vi } from 'vitest'
import { goal } from '@/test/goal-fixtures'
import { loadGoalHistory, reportHistoryWindows } from './goal-history'

const api = vi.hoisted(() => ({
  accounts: { get: vi.fn(), balanceHistory: vi.fn() },
  assets: { get: vi.fn(), list: vi.fn(), valueTrend: vi.fn() },
  transactions: { list: vi.fn() },
  reports: { dataRange: vi.fn(), netWorth: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
const today = '2026-11-21'
/**
 * Builds the server-account goal used by the history API tests.
 *
 * @returns {import('@/types').Goal} Account-linked goal fixture.
 */
const accountGoal = () => goal({ tracking_type: 'account', account_id: 'server' }, today)
beforeEach(() => {
  vi.resetAllMocks()
  api.accounts.get.mockResolvedValue({ currency: 'USD' })
  api.accounts.balanceHistory.mockResolvedValue([
    { date: '2020-01-01', balance: 500 },
    { date: today, balance: 2700 },
  ])
  api.transactions.list.mockResolvedValue({ items: [{ date: '2020-01-01' }] })
  api.assets.get.mockResolvedValue({ currency: 'USD' })
  api.assets.list.mockResolvedValue([])
  api.assets.valueTrend.mockResolvedValue([
    { date: '2020-01-01', amount: 100 },
    { date: today, amount: 8000 },
  ])
  api.reports.dataRange.mockResolvedValue({ min_date: '2020-01-01', max_date: today })
  api.reports.netWorth.mockImplementation(
    async (_months, _interval, _accounts, _groups, _assets, _from, to) => ({
      meta: { currency: 'USD' },
      trend: [{ date: to.slice(0, 7), value: 25700 }],
    }),
  )
})

describe('existing history APIs', () => {
  it('requests all account history from its earliest bank date, including opening balances', async () => {
    const result = await loadGoalHistory(accountGoal(), today)
    expect(result.points).toEqual([
      { date: '2020-01-01', amount: 500 },
      { date: today, amount: 2700 },
    ])
    expect(api.transactions.list).toHaveBeenCalledWith({
      account_id: 'server',
      sort_by: 'transaction_date',
      sort_dir: 'asc',
      limit: 1,
      include_opening_balance: true,
    })
    expect(api.accounts.balanceHistory).toHaveBeenCalledWith('server', '2020-01-01', today)
  })

  it('uses today when the account has no transactions', async () => {
    api.transactions.list.mockResolvedValue({ items: [] })
    await loadGoalHistory(accountGoal(), today)
    expect(api.accounts.balanceHistory).toHaveBeenCalledWith('server', today, today)
  })

  it('keeps older asset history without imposing a rolling window', async () => {
    const result = await loadGoalHistory(
      goal({ tracking_type: 'asset', asset_id: 'studio' }, today),
      today,
    )
    expect(result.points[0]).toEqual({ date: '2020-01-01', amount: 100 })
    expect(result.points.at(-1)?.amount).toBe(8000)
    expect(api.assets.valueTrend).toHaveBeenCalledWith('studio')
  })

  it('sums only nonarchived assets linked to this wallet', async () => {
    api.assets.list.mockResolvedValue([
      { id: 'fund', group_id: 'wallet', currency: 'USD', purchase_date: null, sell_date: null },
      { id: 'bond', group_id: 'wallet', currency: 'USD', purchase_date: null, sell_date: null },
      { id: 'other', group_id: 'other', currency: 'USD' },
      { id: 'archived', group_id: 'wallet', currency: 'USD', is_archived: true },
    ])
    const result = await loadGoalHistory(
      goal({ tracking_type: 'asset_group', asset_group_id: 'wallet' }, today),
      today,
    )
    expect(result.points.at(-1)?.amount).toBe(16000)
    expect(api.assets.valueTrend.mock.calls).toEqual([['fund'], ['bond']])
  })

  it('loads net worth in adjacent ranges, including asset history before transactions', async () => {
    api.assets.list.mockResolvedValue([{ id: 'old-asset', purchase_date: '1995-07-19' }])
    const result = await loadGoalHistory(goal({ tracking_type: 'net_worth' }, today), today)
    expect(result.points.at(-1)?.amount).toBe(25700)
    expect(api.reports.netWorth.mock.calls.map(call => call.slice(5))).toEqual([
      ['1995-07-19', '2004-12-31'],
      ['2005-01-01', '2014-12-31'],
      ['2015-01-01', '2024-12-31'],
      ['2025-01-01', today],
    ])
  })

  it('does not invent manual history', async () => {
    expect(await loadGoalHistory(goal({}, today), today)).toEqual({ points: [], notes: ['manual'] })
    expect(api.transactions.list).not.toHaveBeenCalled()
  })

  it('uses month-end snapshots for the last three months and today for the current month', async () => {
    api.reports.netWorth.mockResolvedValue({
      meta: { currency: 'USD' },
      trend: [
        { date: '2026-07', value: 35200 },
        { date: '2026-08', value: 38300 },
        { date: '2026-09', value: 41300 },
        { date: '2026-10', value: 43800 },
        { date: '2026-11', value: 46900 },
      ],
    })
    expect(await loadGoalHistory(goal({ tracking_type: 'net_worth' }, today), today)).toEqual({
      points: [
        { date: '2026-08-31', amount: 38300 },
        { date: '2026-09-30', amount: 41300 },
        { date: '2026-10-31', amount: 43800 },
        { date: today, amount: 46900 },
      ],
      notes: ['netWorth'],
    })
  })

  it('starts at a more recent first positive month without hiding subsequent losses', async () => {
    api.reports.netWorth.mockResolvedValue({
      meta: { currency: 'USD' },
      trend: [
        { date: '2026-08', value: -100 },
        { date: '2026-09', value: 0 },
        { date: '2026-10', value: 100 },
        { date: '2026-11', value: -50 },
      ],
    })
    expect(
      (await loadGoalHistory(goal({ tracking_type: 'net_worth' }, today), today)).points,
    ).toEqual([
      { date: '2026-10-31', amount: 100 },
      { date: today, amount: -50 },
    ])
  })

  it('retains recent history when net worth has never been positive', async () => {
    api.reports.netWorth.mockResolvedValue({
      meta: { currency: 'USD' },
      trend: [
        { date: '2026-07', value: -300 },
        { date: '2026-08', value: -200 },
        { date: '2026-11', value: 0 },
      ],
    })
    expect(
      (await loadGoalHistory(goal({ tracking_type: 'net_worth' }, today), today)).points,
    ).toEqual([
      { date: '2026-08-31', amount: -200 },
      { date: today, amount: 0 },
    ])
  })

  it('handles year boundaries, leap years and empty net-worth history', async () => {
    api.reports.netWorth.mockResolvedValue({
      meta: { currency: 'USD' },
      trend: [
        { date: '2023-12', value: 50 },
        { date: '2024-02', value: 100 },
        { date: '2024-03', value: 200 },
      ],
    })
    expect(
      (await loadGoalHistory(goal({ tracking_type: 'net_worth' }, '2024-03-15'), '2024-03-15'))
        .points,
    ).toEqual([
      { date: '2023-12-31', amount: 50 },
      { date: '2024-02-29', amount: 100 },
      { date: '2024-03-15', amount: 200 },
    ])
    api.reports.netWorth.mockResolvedValue({ meta: { currency: 'USD' }, trend: [] })
    expect(await loadGoalHistory(goal({ tracking_type: 'net_worth' }, today), today)).toEqual({
      points: [],
      notes: ['netWorth', 'missing'],
    })
  })

  it('withholds currency-mismatched account, asset and wallet history', async () => {
    api.accounts.get.mockResolvedValue({ currency: 'EUR' })
    api.assets.get.mockResolvedValue({ currency: 'EUR' })
    api.assets.list.mockResolvedValue([{ id: 'fund', group_id: 'wallet', currency: 'EUR' }])
    for (const g of [
      accountGoal(),
      goal({ tracking_type: 'asset', asset_id: 'fund' }, today),
      goal({ tracking_type: 'asset_group', asset_group_id: 'wallet' }, today),
    ]) {
      expect((await loadGoalHistory(g, today)).notes).toContain('currency')
    }
    expect(api.accounts.balanceHistory).not.toHaveBeenCalled()
    expect(api.assets.valueTrend).not.toHaveBeenCalled()
  })

  it('withholds net worth when report currency differs from goal currency', async () => {
    expect(
      (await loadGoalHistory(goal({ tracking_type: 'net_worth', currency: 'EUR' }, today), today))
        .notes,
    ).toContain('currency')
  })

  it('does not show partial wallet totals when a member has no valuations', async () => {
    api.assets.list.mockResolvedValue([
      { id: 'fund', group_id: 'wallet', currency: 'USD' },
      { id: 'bond', group_id: 'wallet', currency: 'USD' },
    ])
    api.assets.valueTrend
      .mockResolvedValueOnce([{ date: today, amount: 100 }])
      .mockResolvedValueOnce([])
    const result = await loadGoalHistory(
      goal({ tracking_type: 'asset_group', asset_group_id: 'wallet' }, today),
      today,
    )
    expect(result.points).toEqual([])
    expect(result.notes).toContain('partial')
  })

  it('propagates failures for an explicit UI warning', async () => {
    api.accounts.balanceHistory.mockRejectedValue(new Error('offline'))
    await expect(loadGoalHistory(accountGoal(), today)).rejects.toThrow('offline')
  })
})

it('splits long net-worth history into complete API-compatible ranges', () => {
  expect(reportHistoryWindows('1995-07-19', today)).toEqual([
    ['1995-07-19', '2004-12-31'],
    ['2005-01-01', '2014-12-31'],
    ['2015-01-01', '2024-12-31'],
    ['2025-01-01', today],
  ])
})
