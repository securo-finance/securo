import { format, parseISO } from 'date-fns'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { goal } from '@/test/goal-fixtures'
import { loadGoalHistory } from './goal-history'

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
      trend: [{ date: format(parseISO(to), "RRRR-'W'II"), value: 25700 }],
    }),
  )
})

describe('existing history APIs', () => {
  it('requests account history from one month before goal creation', async () => {
    const result = await loadGoalHistory(accountGoal(), today)
    expect(result.points).toEqual([{ date: today, amount: 2700 }])
    expect(api.accounts.balanceHistory).toHaveBeenCalledWith('server', '2026-02-01', today)
    expect(api.transactions.list).not.toHaveBeenCalled()
  })

  it.each(['credit_card', 'loan'])(
    'converts connected %s history to the negative-debt convention used by goals',
    async type => {
      api.accounts.get.mockResolvedValue({ currency: 'USD', type, connection_id: 'bank' })
      api.accounts.balanceHistory.mockResolvedValue([
        { date: '2026-09-01', balance: -25 },
        { date: '2026-10-01', balance: 0 },
        { date: today, balance: 530.95 },
      ])
      const result = await loadGoalHistory(
        goal({ tracking_type: 'account', account_id: 'card', current_amount: -530.95 }, today),
        today,
      )
      expect(result.points).toEqual([
        { date: '2026-09-01', amount: 25 },
        { date: '2026-10-01', amount: 0 },
        { date: today, amount: -530.95 },
      ])
    },
  )

  it.each([
    { type: 'credit_card', connection_id: null },
    { type: 'loan', connection_id: null },
    { type: 'checking', connection_id: 'bank' },
    { type: 'checking', connection_id: null },
  ])('preserves signed history for $type with connection $connection_id', async account => {
    api.accounts.get.mockResolvedValue({ currency: 'USD', ...account })
    api.accounts.balanceHistory.mockResolvedValue([
      { date: '2026-10-01', balance: 25 },
      { date: today, balance: -530.95 },
    ])
    const result = await loadGoalHistory(accountGoal(), today)
    expect(result.points).toEqual([
      { date: '2026-10-01', amount: 25 },
      { date: today, amount: -530.95 },
    ])
  })

  it('filters asset history to one month before goal creation', async () => {
    const result = await loadGoalHistory(
      goal({ tracking_type: 'asset', asset_id: 'studio' }, today),
      today,
    )
    expect(result.points).toEqual([
      { date: '2026-02-01', amount: 100 },
      { date: today, amount: 8000 },
    ])
    expect(api.assets.valueTrend).toHaveBeenCalledWith('studio', 10)
  })

  it('carries the last valuation from before the window into its start', async () => {
    api.assets.valueTrend.mockResolvedValue([
      { date: '2025-07-28', amount: 3900 },
      { date: '2025-11-28', amount: 4200 },
      { date: '2026-01-28', amount: 4800 },
    ])
    const result = await loadGoalHistory(
      goal({ tracking_type: 'asset', asset_id: 'studio' }, today),
      today,
    )
    expect(result.points).toEqual([{ date: '2026-02-01', amount: 4800 }])
    expect(result.notes).toEqual(['reconstructed'])
  })

  it('requests the whole goal window from the value-trend API', async () => {
    const result = await loadGoalHistory(
      goal(
        {
          tracking_type: 'asset',
          asset_id: 'studio',
          created_at: '2016-01-05T12:00:00Z',
        },
        today,
      ),
      today,
    )
    // Ten years, capped at the endpoint's limit, instead of the 12-month default,
    // and the 2020 valuation stays inside the returned window.
    expect(api.assets.valueTrend).toHaveBeenCalledWith('studio', 120)
    expect(result.points).toEqual([
      { date: '2020-01-01', amount: 100 },
      { date: today, amount: 8000 },
    ])
  })

  it('uses weekly report totals for only the selected wallet, starting at an older goal creation date', async () => {
    api.assets.list.mockResolvedValue([
      { id: 'fund', group_id: 'wallet', currency: 'EUR', purchase_date: null },
      { id: 'other', group_id: 'other', currency: 'USD' },
      { id: 'archived', group_id: 'wallet', is_archived: true },
    ])
    api.assets.valueTrend.mockResolvedValue([{ date: '2020-01-01', amount: 999 }])
    api.reports.netWorth.mockResolvedValue({
      meta: { currency: 'USD' },
      trend: [
        { date: '2020-W01', value: 120 },
        { date: '2020-W02', value: 130 },
        { date: '2026-W47', value: 16000 },
      ],
    })
    const result = await loadGoalHistory(
      goal(
        {
          tracking_type: 'asset_group',
          asset_group_id: 'wallet',
          created_at: '2020-02-01T12:00:00Z',
        },
        today,
      ),
      today,
    )
    expect(result.points).toEqual([
      { date: '2020-01-01', amount: 120 },
      { date: '2020-01-08', amount: 130 },
      { date: today, amount: 16000 },
    ])
    expect(api.assets.valueTrend).not.toHaveBeenCalled()
    expect(api.reports.netWorth).toHaveBeenCalledWith(
      undefined,
      'weekly',
      undefined,
      ['wallet'],
      undefined,
      '2020-01-01',
      today,
    )
    expect(api.transactions.list).not.toHaveBeenCalled()
  })

  it.each(['2021-01-02', '2021-01-04'])(
    'restores ISO week-year boundaries and the exact final snapshot on %s',
    async end => {
      api.assets.list.mockResolvedValue([
        { id: 'fund', group_id: 'wallet', purchase_date: '2020-12-25' },
      ])
      api.assets.valueTrend.mockResolvedValue([])
      api.reports.netWorth.mockResolvedValue({
        meta: { currency: 'USD' },
        trend: [
          { date: '2020-W52', value: 100 },
          { date: '2020-W53', value: 200 },
          ...(end === '2021-01-04' ? [{ date: '2021-W01', value: 300 }] : []),
        ],
      })
      const result = await loadGoalHistory(
        goal(
          {
            tracking_type: 'asset_group',
            asset_group_id: 'wallet',
            created_at: '2020-01-27T12:00:00Z',
          },
          end,
        ),
        end,
      )
      expect(result.points).toEqual(
        end === '2021-01-02'
          ? [
              { date: '2020-12-25', amount: 100 },
              { date: end, amount: 200 },
            ]
          : [
              { date: '2020-12-25', amount: 100 },
              { date: '2021-01-01', amount: 200 },
              { date: end, amount: 300 },
            ],
      )
    },
  )

  it('caps old wallet history at ten years in one request', async () => {
    api.assets.list.mockResolvedValue([
      { id: 'fund', group_id: 'wallet', purchase_date: '1995-07-19' },
    ])
    await loadGoalHistory(
      goal(
        {
          tracking_type: 'asset_group',
          asset_group_id: 'wallet',
          created_at: '1995-08-19T12:00:00Z',
        },
        today,
      ),
      today,
    )
    expect(api.reports.netWorth.mock.calls.map(call => call.slice(5))).toEqual([
      ['2016-11-21', today],
    ])
    expect(
      api.reports.netWorth.mock.calls.every(
        call => call[1] === 'weekly' && call[3][0] === 'wallet',
      ),
    ).toBe(true)
  })

  it.each([
    { end: '2026-11-21', created: '2026-10-01T12:00:00Z', start: '2026-09-01' },
    { end: '2026-11-21', created: '2022-04-10T12:00:00Z', start: '2022-03-10' },
    { end: '2026-11-21', created: '2026-11-21T12:00:00Z', start: '2026-10-21' },
    { end: '2024-02-29', created: '2024-02-01T12:00:00Z', start: '2024-01-01' },
    { end: '2024-03-31', created: '2024-03-31T12:00:00Z', start: '2024-02-29' },
  ])('uses $start for a wallet goal created at $created', async ({ end, created, start }) => {
    api.assets.list.mockResolvedValue([
      { id: 'fund', group_id: 'wallet', purchase_date: '1990-01-01' },
    ])
    const wallet = goal(
      {
        tracking_type: 'asset_group',
        asset_group_id: 'wallet',
        created_at: created,
      },
      end,
    )
    await loadGoalHistory(wallet, end)
    expect(api.reports.netWorth).toHaveBeenCalledWith(
      undefined,
      'weekly',
      undefined,
      ['wallet'],
      undefined,
      start,
      end,
    )
    expect(api.assets.valueTrend).not.toHaveBeenCalled()
    expect(api.transactions.list).not.toHaveBeenCalled()
  })

  it('caps old net-worth history at ten years in one request', async () => {
    const result = await loadGoalHistory(
      goal({ tracking_type: 'net_worth', created_at: '1995-08-19T00:00:00Z' }, today),
      today,
    )
    expect(api.assets.valueTrend).not.toHaveBeenCalled()
    expect(api.assets.list).not.toHaveBeenCalled()
    expect(api.transactions.list).not.toHaveBeenCalled()
    expect(result.points.at(-1)?.amount).toBe(25700)
    expect(api.reports.netWorth.mock.calls.map(call => call.slice(5))).toEqual([
      ['2016-11-21', today],
    ])
  })

  it('does not invent manual history', async () => {
    expect(await loadGoalHistory(goal({}, today), today)).toEqual({ points: [], notes: ['manual'] })
    expect(api.transactions.list).not.toHaveBeenCalled()
  })

  it('uses weekly snapshots anchored to the start weekday and today for the final week', async () => {
    api.reports.netWorth.mockResolvedValue({
      meta: { currency: 'USD' },
      trend: [
        { date: '2026-W44', value: 35200 },
        { date: '2026-W45', value: 38300 },
        { date: '2026-W46', value: 41300 },
        { date: '2026-W47', value: 46900 },
      ],
    })
    expect(await loadGoalHistory(goal({ tracking_type: 'net_worth' }, today), today)).toEqual({
      points: [
        { date: '2026-11-01', amount: 35200 },
        { date: '2026-11-08', amount: 38300 },
        { date: '2026-11-15', amount: 41300 },
        { date: today, amount: 46900 },
      ],
      notes: ['netWorth'],
    })
    expect(api.reports.netWorth).toHaveBeenCalledExactlyOnceWith(
      undefined,
      'weekly',
      undefined,
      undefined,
      undefined,
      '2026-02-01',
      today,
    )
  })

  it('filters weekly snapshots before the goal history window and retains losses', async () => {
    api.reports.netWorth.mockResolvedValue({
      meta: { currency: 'USD' },
      trend: [
        { date: '2026-W42', value: -100 },
        { date: '2026-W43', value: 0 },
        { date: '2026-W44', value: 100 },
        { date: '2026-W47', value: -50 },
      ],
    })
    const result = await loadGoalHistory(
      goal({ tracking_type: 'net_worth', created_at: today + 'T00:00:00Z' }, today),
      today,
    )
    expect(result.points).toEqual([
      { date: '2026-10-21', amount: 0 },
      { date: '2026-10-28', amount: 100 },
      { date: today, amount: -50 },
    ])
  })

  it('retains weekly history when net worth has never been positive', async () => {
    api.reports.netWorth.mockResolvedValue({
      meta: { currency: 'USD' },
      trend: [
        { date: '2026-W44', value: -300 },
        { date: '2026-W45', value: -200 },
        { date: '2026-W47', value: 0 },
      ],
    })
    const result = await loadGoalHistory(goal({ tracking_type: 'net_worth' }, today), today)
    expect(result.points).toEqual([
      { date: '2026-11-01', amount: -300 },
      { date: '2026-11-08', amount: -200 },
      { date: today, amount: 0 },
    ])
  })

  it.each([
    { created: '2026-11-20', start: '2026-10-20', first: '2026-10-27' },
    { created: '2026-08-01', start: '2026-07-01', first: '2026-10-28' },
    { created: '2024-01-01', start: '2023-12-01', first: '2026-10-30' },
  ])('anchors weekly net worth to goal creation: $created', async ({ created, start, first }) => {
    api.reports.netWorth.mockResolvedValue({
      meta: { currency: 'USD' },
      trend: [{ date: '2026-W44', value: -50 }],
    })
    const result = await loadGoalHistory(
      goal({ tracking_type: 'net_worth', created_at: created + 'T00:00:00Z' }, today),
      today,
    )
    expect(result.points).toEqual([{ date: first, amount: -50 }])
    expect(api.reports.netWorth).toHaveBeenCalledExactlyOnceWith(
      undefined,
      'weekly',
      undefined,
      undefined,
      undefined,
      start,
      today,
    )
  })

  it.each([
    {
      created: '2021-01-25',
      end: '2021-01-04',
      trend: [
        { date: '2020-W53', value: 100 },
        { date: '2021-W01', value: 200 },
      ],
      expected: [
        { date: '2021-01-01', amount: 100 },
        { date: '2021-01-04', amount: 200 },
      ],
    },
    {
      created: '2024-03-29',
      end: '2024-03-08',
      trend: [
        { date: '2024-W09', value: 100 },
        { date: '2024-W10', value: 200 },
      ],
      expected: [
        { date: '2024-02-29', amount: 100 },
        { date: '2024-03-08', amount: 200 },
      ],
    },
  ])(
    'restores weekly year and leap-day boundaries ending $end',
    async ({ created, end, trend, expected }) => {
      api.reports.netWorth.mockResolvedValue({ meta: { currency: 'USD' }, trend })
      const result = await loadGoalHistory(
        goal({ tracking_type: 'net_worth', created_at: created + 'T00:00:00Z' }, end),
        end,
      )
      expect(result.points).toEqual(expected)
    },
  )

  it('reports missing net-worth history for an empty weekly report', async () => {
    api.reports.netWorth.mockResolvedValue({ meta: { currency: 'USD' }, trend: [] })
    expect(await loadGoalHistory(goal({ tracking_type: 'net_worth' }, today), today)).toEqual({
      points: [],
      notes: ['netWorth', 'missing'],
    })
  })

  it('withholds currency-mismatched account and asset history', async () => {
    api.accounts.get.mockResolvedValue({ currency: 'EUR' })
    api.assets.get.mockResolvedValue({ currency: 'EUR' })
    api.assets.list.mockResolvedValue([{ id: 'fund', group_id: 'wallet', currency: 'EUR' }])
    for (const g of [accountGoal(), goal({ tracking_type: 'asset', asset_id: 'fund' }, today)]) {
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

  it('withholds wallet totals when the report currency differs from the goal', async () => {
    api.assets.list.mockResolvedValue([{ id: 'fund', group_id: 'wallet' }])
    const result = await loadGoalHistory(
      goal({ tracking_type: 'asset_group', asset_group_id: 'wallet', currency: 'EUR' }, today),
      today,
    )
    expect(result).toEqual({ points: [], notes: ['currency'] })
  })

  it('reports missing wallet history for an empty wallet or empty report', async () => {
    const wallet = goal({ tracking_type: 'asset_group', asset_group_id: 'wallet' }, today)
    expect((await loadGoalHistory(wallet, today)).notes).toContain('missing')
    expect(api.reports.netWorth).not.toHaveBeenCalled()
    api.assets.list.mockResolvedValue([{ id: 'fund', group_id: 'wallet' }])
    api.reports.netWorth.mockResolvedValue({ meta: { currency: 'USD' }, trend: [] })
    expect((await loadGoalHistory(wallet, today)).notes).toContain('missing')
  })

  it('propagates wallet report failures for retry', async () => {
    api.assets.list.mockResolvedValue([{ id: 'fund', group_id: 'wallet' }])
    api.reports.netWorth.mockRejectedValue(new Error('offline'))
    await expect(
      loadGoalHistory(
        goal({ tracking_type: 'asset_group', asset_group_id: 'wallet' }, today),
        today,
      ),
    ).rejects.toThrow('offline')
  })

  it('propagates failures for an explicit UI warning', async () => {
    api.accounts.balanceHistory.mockRejectedValue(new Error('offline'))
    await expect(loadGoalHistory(accountGoal(), today)).rejects.toThrow('offline')
  })
})
