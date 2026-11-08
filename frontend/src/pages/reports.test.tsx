import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'

import ReportsPage from '@/pages/reports'
import { renderWithProviders, t } from '@/test/utils'
import type { ReportResponse } from '@/types'

const api = vi.hoisted(() => ({
  reports: {
    netWorth: vi.fn(),
    incomeExpenses: vi.fn(),
    cashFlow: vi.fn(),
    dataRange: vi.fn(),
  },
  // The page reads the workspace timezone to tell a balance that is already
  // negative from one that crosses later. Unresolved here, which lands on the
  // browser's own day.
  timezones: { list: vi.fn().mockResolvedValue({ timezones: [], default: null }) },
}))

vi.mock('@/lib/api', () => ({ reports: api.reports, timezones: api.timezones }))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

const privacy = vi.hoisted(() => ({ on: false }))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: privacy.on, MASK: '••••' }),
}))

function emptyReport(type: string): ReportResponse {
  return {
    summary: { primary_value: 1000, change_amount: 100, change_percent: 5, breakdowns: [] },
    trend: [{ date: '2026-01-01', value: 1000, breakdowns: {}, change: null }],
    meta: { type, series_keys: [], currency: 'USD', interval: 'monthly' },
    composition: [],
    category_trend: [],
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 12, 12))
  vi.clearAllMocks()
  privacy.on = false
  api.reports.netWorth.mockResolvedValue(emptyReport('net_worth'))
  api.reports.incomeExpenses.mockResolvedValue(emptyReport('income_expenses'))
  api.reports.cashFlow.mockResolvedValue(emptyReport('cash_flow'))
  // No data by default: the year picker stays hidden unless a test opts in.
  api.reports.dataRange.mockResolvedValue({ min_date: null, max_date: null })
})

afterEach(() => vi.useRealTimers())

describe('Reports page — Custom range segment', () => {
  it('loads the Net Worth tab with the 1Y preset by default', async () => {
    renderWithProviders(<ReportsPage />)

    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())
    const [, , , , , startDate, endDate] = api.reports.netWorth.mock.calls[0]
    expect(startDate).toBeUndefined()
    expect(endDate).toBeUndefined()
  })

  it('opening Custom waits for Apply before querying January 1 through today', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))

    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))

    expect(api.reports.netWorth).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: t('reports.range1y') })).toHaveClass('bg-primary')
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    const year = 2026
    await waitFor(() => {
      const last = api.reports.netWorth.mock.calls.at(-1)!
      expect(last[5]).toBe(`${year}-01-01`)
      expect(last[6]).toBe('2026-09-12')
    })

    // The segment itself now displays the picked range instead of "Custom
    // range" — in the same compact, year-less form as the transactions
    // filter bar's applied-range chip.
    const fmt = (iso: string) =>
      new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', {
        day: '2-digit',
        month: 'short',
      })
    expect(
      screen.getByRole('button', { name: t('reports.customRange') }),
    ).toHaveTextContent(`${fmt(`${year}-01-01`)} - ${fmt('2026-09-12')}`)
  })

  it('is not offered on the Cash Flow tab', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())

    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))

    await waitFor(() => expect(api.reports.cashFlow).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: t('reports.customRange') })).not.toBeInTheDocument()
  })
})


it.each(['Cancel', 'Escape', 'outside'] as const)(
  '6M → Custom → %s leaves the preset and query unchanged', async (dismiss) => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))
    const preset = screen.getByRole('button', { name: t('reports.range6m') })
    await user.click(preset)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(2))
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    if (dismiss === 'Cancel') {
      await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.cancel') }))
    } else if (dismiss === 'Escape') {
      await user.keyboard('{Escape}')
    } else {
      await user.click(document.body)
    }
    expect(preset).toHaveClass('bg-primary')
    expect(api.reports.netWorth).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    expect(api.reports.netWorth).toHaveBeenCalledTimes(2)
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(3))
  },
)

it.each([
  ['reports.netWorth', 'reports.range1y', 12],
  ['reports.incomeExpenses', 'reports.range1y', 12],
  ['reports.moneyMap', 'reports.range3m', 3],
] as const)('Reset then Apply restores the fallback for %s', async (tab, fallback, months) => {
  const { user } = renderWithProviders(<ReportsPage />)
  if (tab !== 'reports.netWorth') {
    await user.click(screen.getByRole('button', { name: t(tab) }))
  }
  const custom = screen.getByRole('button', { name: t('reports.customRange') })
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  const reportApi = tab === 'reports.netWorth' ? api.reports.netWorth : api.reports.incomeExpenses
  await waitFor(() => expect(reportApi.mock.calls.at(-1)?.slice(-2)).toEqual([
    '2026-01-01', '2026-09-12',
  ]))
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.reset') }))
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  await waitFor(() => {
    expect(screen.getByRole('button', { name: t(fallback) })).toHaveClass('bg-primary')
    expect(reportApi.mock.calls.at(-1)?.[0]).toBe(months)
    expect(reportApi.mock.calls.at(-1)?.slice(-2)).toEqual([undefined, undefined])
  })
  expect(custom).toHaveTextContent(t('reports.customRange'))
})

it('preserves saved custom dates across supported tabs and preset dismissal', async () => {
  const { user } = renderWithProviders(<ReportsPage />)
  const custom = screen.getByRole('button', { name: t('reports.customRange') })
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  for (const tab of ['reports.incomeExpenses', 'reports.moneyMap']) {
    await user.click(screen.getByRole('button', { name: t(tab) }))
    await waitFor(() => expect(api.reports.incomeExpenses.mock.calls.at(-1)?.slice(-2))
      .toEqual(['2026-01-01', '2026-09-12']))
    expect(custom).toHaveClass('bg-primary')
  }
  await user.click(screen.getByRole('button', { name: t('reports.range6m') }))
  await waitFor(() => expect(api.reports.incomeExpenses.mock.calls.at(-1)?.slice(-2))
    .toEqual([undefined, undefined]))
  const count = api.reports.incomeExpenses.mock.calls.length
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.reset') }))
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.cancel') }))
  expect(api.reports.incomeExpenses).toHaveBeenCalledTimes(count)
  expect(screen.getByRole('button', { name: t('reports.range6m') })).toHaveClass('bg-primary')
  await user.click(custom)
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  await waitFor(() => expect(api.reports.incomeExpenses.mock.calls.at(-1)?.slice(-2))
    .toEqual(['2026-01-01', '2026-09-12']))
  await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))
  await waitFor(() => expect(api.reports.cashFlow).toHaveBeenCalled())
  expect(screen.queryByRole('button', { name: t('reports.customRange') })).not.toBeInTheDocument()
  expect(api.reports.cashFlow.mock.calls.at(-1)?.[0]).toBe(6)
  await user.click(screen.getByRole('button', { name: t('reports.netWorth') }))
  await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
  await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
  await waitFor(() => expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2))
    .toEqual(['2026-01-01', '2026-09-12']))
})

describe('Reports page — query failure', () => {
  it('surfaces a rejected custom range instead of showing stale numbers', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))

    api.reports.netWorth.mockRejectedValueOnce({
      response: { data: { detail: 'Custom range is too wide (max 10 years)' } },
    })
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))

    expect(await screen.findByText('Custom range is too wide (max 10 years)')).toBeInTheDocument()
    // The prior successful report must not linger under the error.
    expect(screen.queryByText(t('reports.trend'), { exact: false })).not.toBeInTheDocument()
    // Tabs and range controls stay usable so the user can correct the request.
    expect(screen.getByRole('button', { name: t('reports.range1y') })).toBeInTheDocument()
  })

  it('shows a generic fallback for a network failure and recovers via Retry', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))

    api.reports.netWorth.mockRejectedValueOnce(new Error('network error'))
    await user.click(screen.getByRole('button', { name: t('reports.range6m') }))

    expect(await screen.findByText(t('reports.loadError'))).toBeInTheDocument()

    api.reports.netWorth.mockResolvedValueOnce(emptyReport('net_worth'))
    await user.click(screen.getByRole('button', { name: t('common.retry') }))

    await waitFor(() => expect(screen.queryByText(t('reports.loadError'))).not.toBeInTheDocument())
  })

  it('recovers by changing the range after a rejected selection', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalledTimes(1))

    api.reports.netWorth.mockRejectedValueOnce({ response: { data: { detail: 'end_date must be on or before today' } } })
    await user.click(screen.getByRole('button', { name: t('reports.customRange') }))
    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.apply') }))
    expect(await screen.findByText('end_date must be on or before today')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('reports.range6m') }))
    await waitFor(() => expect(screen.queryByText('end_date must be on or before today')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: t('reports.range6m') })).toHaveClass('bg-primary')
  })
})

describe('Reports page, cash-flow runway', () => {
  async function openCashFlow(meta: Partial<ReportResponse['meta']>) {
    const report = emptyReport('cash_flow')
    api.reports.cashFlow.mockResolvedValue({ ...report, meta: { ...report.meta, ...meta } })
    const { user } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())
    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))
    return screen.findByTestId('cash-flow-runway')
  }

  it('names the day the balance goes negative and its lowest point', async () => {
    const callout = await openCashFlow({
      runway_date: '2099-10-05',
      lowest_balance: -500,
      lowest_balance_date: '2099-12-20',
    })

    expect(callout).toHaveTextContent(t('reports.runwayNegativeOn', { date: 'Oct 5, 2099' }))
    expect(callout).toHaveTextContent('-$500.00')
    // Only the crossing date is shown: a second date next to the amount reads
    // as part of the same sentence rather than as the bottom of the dip.
    expect(callout).not.toHaveTextContent('Dec 20')
  })

  it('reads today in the workspace timezone, not the browser one', async () => {
    // 23:30 UTC on the 13th is already the 14th fourteen hours ahead, so the
    // same crossing date is "already negative" there and still ahead in UTC.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-10-13T23:30:00Z'))
    try {
      api.timezones.list.mockResolvedValue({ timezones: [], default: 'Pacific/Kiritimati' })
      const ahead = await openCashFlow({
        runway_date: '2026-10-14',
        lowest_balance: -10,
        lowest_balance_date: '2026-10-14',
      })
      await waitFor(() => expect(ahead).toHaveTextContent(t('reports.runwayAlreadyNegative')))

      cleanup()
      api.timezones.list.mockResolvedValue({ timezones: [], default: 'UTC' })
      const behind = await openCashFlow({
        runway_date: '2026-10-14',
        lowest_balance: -10,
        lowest_balance_date: '2026-10-14',
      })
      await waitFor(() =>
        expect(behind).toHaveTextContent(t('reports.runwayNegativeOn', { date: 'Oct 14' })),
      )
    } finally {
      vi.useRealTimers()
      api.timezones.list.mockResolvedValue({ timezones: [], default: null })
    }
  })

  it('says the balance is already negative rather than naming today', async () => {
    const callout = await openCashFlow({
      runway_date: '2020-01-02',
      lowest_balance: -500,
      lowest_balance_date: '2020-01-31',
    })

    expect(callout).toHaveTextContent(t('reports.runwayAlreadyNegative'))
    expect(callout).not.toHaveTextContent(t('reports.runwayNegativeOn', { date: 'Jan 2' }))
  })

  it.each([
    [400, '$400.00'],
    [0, '$0.00'],
  ])('reports a lowest balance of %d as never going below zero', async (lowest, amount) => {
    const callout = await openCashFlow({
      runway_date: null,
      lowest_balance: lowest,
      lowest_balance_date: '2026-12-01',
    })

    expect(callout).toHaveTextContent(t('reports.lowestBalance'))
    expect(callout).toHaveTextContent(amount)
    expect(callout).toHaveTextContent('Dec 1')
  })

  it('is not shown on the other tabs', async () => {
    api.reports.netWorth.mockResolvedValue({
      ...emptyReport('net_worth'),
      meta: { ...emptyReport('net_worth').meta, lowest_balance: 1, lowest_balance_date: '2026-10-01' },
    })
    renderWithProviders(<ReportsPage />)
    await screen.findAllByText('$1,000.00')

    expect(screen.queryByTestId('cash-flow-runway')).not.toBeInTheDocument()
  })
})

describe('Reports page: month presets and period navigation (issue #519)', () => {
  it('offers This Month and Last Month with exact month windows', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    expect(await screen.findByRole('heading', { name: 'Net Worth' })).toBeInTheDocument()

    // The default preset (1Y) keeps the API-anchored window: no explicit dates.
    expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2)).toEqual([undefined, undefined])

    await user.click(screen.getByRole('button', { name: 'This Month' }))
    expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2)).toEqual(['2026-09-01', '2026-09-12'])
    expect(screen.getByTestId('reports-period-label')).toHaveTextContent('September 2026')

    await user.click(screen.getByRole('button', { name: 'Last Month' }))
    expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2)).toEqual(['2026-08-01', '2026-08-31'])
    expect(screen.getByTestId('reports-period-label')).toHaveTextContent('August 2026')
  })

  it('steps the active period back and forward with the arrows', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await screen.findByRole('heading', { name: 'Net Worth' })
    await user.click(screen.getByRole('button', { name: 'This Month' }))

    const prev = screen.getByRole('button', { name: 'Previous period' })
    const next = screen.getByRole('button', { name: 'Next period' })
    expect(next).toBeDisabled()

    await user.click(prev)
    expect(screen.getByTestId('reports-period-label')).toHaveTextContent('August 2026')
    expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2)).toEqual(['2026-08-01', '2026-08-31'])
    expect(next).not.toBeDisabled()

    await user.click(next)
    expect(screen.getByTestId('reports-period-label')).toHaveTextContent('September 2026')
    expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2)).toEqual(['2026-09-01', '2026-09-12'])
    expect(next).toBeDisabled()
  })

  it('steps a macro preset back by a whole window and labels the covered span', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await screen.findByRole('heading', { name: 'Net Worth' })
    await user.click(screen.getByRole('button', { name: '6M' }))

    expect(screen.getByTestId('reports-period-label')).toHaveTextContent('6M')
    await user.click(screen.getByRole('button', { name: 'Previous period' }))

    expect(api.reports.netWorth.mock.calls.at(-1)?.slice(-2)).toEqual(['2025-08-01', '2026-02-28'])
    expect(screen.getByTestId('reports-period-label')).toHaveTextContent('Aug 2025 - Feb 2026')
  })

  it('does not carry a stepped window into the Cash Flow forecast', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await screen.findByRole('heading', { name: 'Net Worth' })
    await user.click(screen.getByRole('button', { name: '6M' }))
    await user.click(screen.getByRole('button', { name: 'Previous period' }))

    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))
    await waitFor(() => expect(api.reports.cashFlow).toHaveBeenCalled())

    expect(api.reports.cashFlow.mock.calls.at(-1)?.[0]).toBe(6)
  })

  it('hides period navigation on the forward-only Cash Flow tab', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    await screen.findByRole('heading', { name: 'Net Worth' })
    expect(screen.getByRole('button', { name: 'Previous period' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))
    expect(screen.queryByRole('button', { name: 'Previous period' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('reports-period-nav')).not.toBeInTheDocument()
  })
})

describe('Reports page - year picker (issue #765)', () => {
  beforeEach(() => {
    api.reports.dataRange.mockResolvedValue({ min_date: '2023-04-10', max_date: '2026-09-12' })
  })

  it('lists every year from this year back to the oldest transaction', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    const trigger = await screen.findByRole('button', { name: t('reports.year') })
    await user.click(trigger)
    await screen.findByRole('button', { name: '2026' })
    expect(screen.getByRole('button', { name: '2025' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '2024' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '2023' })).toBeInTheDocument()
  })

  it('commits the picked past year as a Jan 1 to Dec 31 range', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    const trigger = await screen.findByRole('button', { name: t('reports.year') })
    await user.click(trigger)
    await user.click(await screen.findByRole('button', { name: '2023' }))

    // Full calendar year: 364 days, ceil(364/30) = 13 monthly buckets.
    expect(api.reports.netWorth).toHaveBeenCalledWith(
      13, 'monthly', undefined, undefined, undefined, '2023-01-01', '2023-12-31',
    )
    // The custom segment is now the active range and shows the span.
    expect(screen.getByRole('button', { name: t('reports.customRange') })).toHaveClass('bg-primary')
    expect(screen.getByRole('button', { name: t('reports.customRange') })).toHaveTextContent('Jan 01 - Dec 31')
    // The trigger highlights the year that is currently on screen.
    expect(screen.getByRole('button', { name: `${t('reports.year')} 2023` })).toHaveClass('bg-primary')
  })

  it('ends the current year at today', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    const trigger = await screen.findByRole('button', { name: t('reports.year') })
    await user.click(trigger)
    await user.click(await screen.findByRole('button', { name: '2026' }))

    // Jan 1 through today (2026-09-12): 254 days, ceil(254/30) = 9.
    expect(api.reports.netWorth).toHaveBeenCalledWith(
      9, 'monthly', undefined, undefined, undefined, '2026-01-01', '2026-09-12',
    )
  })

  it('hides the picker when the data spans a single year', async () => {
    // Two phases: the same widget shows for multi-year data and disappears
    // once the data no longer covers a previous year.
    renderWithProviders(<ReportsPage />)
    await screen.findByRole('button', { name: t('reports.year') })
    cleanup()

    api.reports.dataRange.mockResolvedValue({ min_date: '2026-01-05', max_date: '2026-09-12' })
    renderWithProviders(<ReportsPage />)
    await screen.findByRole('heading', { name: 'Net Worth' })
    expect(screen.queryByRole('button', { name: t('reports.year') })).not.toBeInTheDocument()
  })

  it('hides the picker when the data range fails to load', async () => {
    renderWithProviders(<ReportsPage />)
    await screen.findByRole('button', { name: t('reports.year') })
    cleanup()

    api.reports.dataRange.mockRejectedValue(new Error('network down'))
    renderWithProviders(<ReportsPage />)
    await screen.findByRole('heading', { name: 'Net Worth' })
    expect(screen.queryByRole('button', { name: t('reports.year') })).not.toBeInTheDocument()
  })

  it('is not offered on the forward-only Cash Flow tab', async () => {
    const { user } = renderWithProviders(<ReportsPage />)
    const trigger = await screen.findByRole('button', { name: t('reports.year') })
    expect(trigger).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('reports.cashFlow') }))
    await screen.findByRole('heading', { name: t('reports.cashFlow') })
    expect(screen.queryByRole('button', { name: t('reports.year') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `${t('reports.year')} 2026` })).not.toBeInTheDocument()
  })
})

describe('Reports page, cash-flow bars end at net flow (issue #1241)', () => {
  const INFLOW_COLOR = 'rgb(16, 185, 129)'
  const OUTFLOW_COLOR = 'rgb(244, 63, 94)'

  // Bars grow in over animation frames. Report every frame as long past the
  // animation so the chart lands on its final geometry straight away.
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
      setTimeout(() => callback(performance.now() + 60_000), 0))
    vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
  })
  afterEach(() => vi.unstubAllGlobals())

  type Segment = { y: number; height: number; rounded: boolean }
  type Column = { x: number; width: number; inflow?: Segment; outflow?: Segment }

  function report(type: string, trend: Array<{ date: string; breakdowns: Record<string, number> }>, seriesKeys: string[] = []) {
    const base = emptyReport(type)
    return {
      ...base,
      meta: { ...base.meta, series_keys: seriesKeys },
      trend: trend.map((point) => ({ ...point, value: 0, change: null })),
    }
  }

  async function openTab(tab?: string) {
    const { user, container } = renderWithProviders(<ReportsPage />)
    await waitFor(() => expect(api.reports.netWorth).toHaveBeenCalled())
    if (tab) await user.click(screen.getByRole('button', { name: t(tab) }))
    return container
  }

  async function openCashFlowBars(periods: Array<[date: string, inflow: number, outflow: number]>) {
    api.reports.cashFlow.mockResolvedValue(
      report('cash_flow', periods.map(([date, inflow, outflow]) => ({ date, breakdowns: { inflow, outflow } }))),
    )
    return barChart(await openTab('reports.cashFlow'))
  }

  // Tabs draw other charts too (balance projection, composition); the
  // stacked bars live in their own one.
  function barChart(container: HTMLElement) {
    return waitFor(() => {
      const found = Array.from(container.querySelectorAll<HTMLElement>('.recharts-wrapper'))
        .find((wrapper) => wrapper.querySelector('.recharts-bar'))
      expect(found).toBeDefined()
      return found!
    })
  }

  function zeroLine(chart: HTMLElement) {
    return Number(chart.querySelector('.recharts-reference-line-line')!.getAttribute('y1'))
  }

  // Recharts draws a downward bar with a negative height; normalise to a top edge.
  function box(path: Element) {
    const y = Number(path.getAttribute('y'))
    const height = Number(path.getAttribute('height'))
    return { y: Math.min(y, y + height), height: Math.abs(height) }
  }

  function columns(chart: HTMLElement): Column[] {
    const byX = new Map<number, Column>()
    for (const path of chart.querySelectorAll('.recharts-bar-rectangle path')) {
      const x = Number(path.getAttribute('x'))
      const column = byX.get(x) ?? { x, width: Number(path.getAttribute('width')) }
      const segment: Segment = { ...box(path), rounded: path.getAttribute('d')!.includes('A') }
      if (path.getAttribute('fill') === '#10B981') column.inflow = segment
      else column.outflow = segment
      byX.set(x, column)
    }
    return [...byX.values()].sort((a, b) => a.x - b.x)
  }

  // jsdom has no layout, which leaves Recharts unable to map the pointer to a
  // period. Give the chart the same box the test viewport renders it at.
  function hoverAt(chart: HTMLElement, clientX: number) {
    Object.defineProperties(chart, {
      offsetWidth: { configurable: true, value: 800 },
      offsetHeight: { configurable: true, value: 300 },
      getBoundingClientRect: { configurable: true, value: () => DOMRect.fromRect({ width: 800, height: 300 }) },
    })
    fireEvent.mouseMove(chart, { clientX, clientY: 100 })
    return chart.querySelector<HTMLElement>('.recharts-tooltip-wrapper')!
  }

  async function hoverColumn(chart: HTMLElement, column: Column) {
    const tooltip = hoverAt(chart, column.x + column.width / 2)
    await waitFor(() => expect(tooltip).not.toBeEmptyDOMElement())
    return tooltip
  }

  function periodX(chart: HTMLElement, date: string) {
    const tick = Array.from(chart.querySelectorAll('.recharts-xAxis-tick-labels text'))
      .find((text) => text.textContent === date)!
    return Number(tick.getAttribute('x'))
  }

  it('ends a surplus above zero, a deficit below it and break-even exactly on it', async () => {
    const chart = await openCashFlowBars([
      ['2026-10-01', 3000, 2500],
      ['2026-11-01', 2500, 3000],
      ['2026-12-01', 2000, 2000],
    ])

    await waitFor(() => {
      const zero = zeroLine(chart)
      const [surplus, deficit, breakEven] = columns(chart)
      const pxPerUnit = surplus.inflow!.height / 3000

      expect(surplus.inflow!.y).toBeCloseTo(zero - 500 * pxPerUnit)
      expect(deficit.inflow!.y).toBeCloseTo(zero + 500 * pxPerUnit)
      expect(breakEven.inflow!.y).toBeCloseTo(zero)
    })
  })

  it('keeps the inflow and outflow segments at their own lengths, stacked without a gap', async () => {
    const chart = await openCashFlowBars([['2026-10-01', 3000, 2500]])

    await waitFor(() => {
      const [{ inflow, outflow }] = columns(chart)

      expect(inflow!.height / outflow!.height).toBeCloseTo(3000 / 2500)
      expect(outflow!.y).toBeCloseTo(inflow!.y + inflow!.height)
    })
  })

  it('rounds whichever segment ends at net flow', async () => {
    const chart = await openCashFlowBars([
      ['2026-10-01', 3000, 2500],
      ['2026-11-01', 0, 1000],
    ])

    await waitFor(() => {
      const zero = zeroLine(chart)
      const [mixed, outflowOnly] = columns(chart)

      expect(mixed.inflow!.rounded).toBe(true)
      expect(mixed.outflow!.rounded).toBe(false)
      expect(outflowOnly.inflow).toBeUndefined()
      expect(outflowOnly.outflow!.rounded).toBe(true)
      expect(outflowOnly.outflow!.y).toBeGreaterThan(zero)
    })
  })

  it('lists inflow, outflow and a net flow coloured by its sign in the tooltip', async () => {
    const chart = await openCashFlowBars([
      ['2026-10-01', 3000, 2500],
      ['2026-11-01', 2500, 3000],
    ])
    let surplus!: Column
    let deficit!: Column
    await waitFor(() => {
      ;[surplus, deficit] = columns(chart)
      expect(deficit).toBeDefined()
    })

    let tooltip = await hoverColumn(chart, deficit)
    expect(tooltip).toHaveTextContent('2026-11-01')
    expect(within(tooltip).getByText(`${t('reports.inflow')}: $2,500.00`)).toHaveStyle({ color: INFLOW_COLOR })
    expect(within(tooltip).getByText(`${t('reports.outflow')}: -$3,000.00`)).toHaveStyle({ color: OUTFLOW_COLOR })
    expect(within(tooltip).getByText(`${t('reports.netFlow')}: -$500.00`)).toHaveStyle({ color: OUTFLOW_COLOR })

    tooltip = await hoverColumn(chart, surplus)
    await waitFor(() => expect(tooltip).toHaveTextContent('2026-10-01'))
    expect(within(tooltip).getByText(`${t('reports.netFlow')}: $500.00`)).toHaveStyle({ color: INFLOW_COLOR })
  })

  it('lists both amounts in the tooltip when a period has only one of them', async () => {
    const chart = await openCashFlowBars([
      ['2026-10-01', 1200, 0],
      ['2026-11-01', 0, 800],
    ])
    let inflowOnly!: Column
    let outflowOnly!: Column
    await waitFor(() => {
      ;[inflowOnly, outflowOnly] = columns(chart)
      expect(outflowOnly).toBeDefined()
    })

    let tooltip = await hoverColumn(chart, inflowOnly)
    expect(within(tooltip).getByText(`${t('reports.inflow')}: $1,200.00`)).toBeInTheDocument()
    expect(within(tooltip).getByText(`${t('reports.outflow')}: $0.00`)).toHaveStyle({ color: OUTFLOW_COLOR })
    expect(within(tooltip).getByText(`${t('reports.netFlow')}: $1,200.00`)).toBeInTheDocument()

    tooltip = await hoverColumn(chart, outflowOnly)
    await waitFor(() => expect(tooltip).toHaveTextContent('2026-11-01'))
    expect(within(tooltip).getByText(`${t('reports.inflow')}: $0.00`)).toHaveStyle({ color: INFLOW_COLOR })
    expect(within(tooltip).getByText(`${t('reports.outflow')}: -$800.00`)).toBeInTheDocument()
    expect(within(tooltip).getByText(`${t('reports.netFlow')}: -$800.00`)).toBeInTheDocument()
  })

  it('lists the outflow in the tooltip even when the range has none to draw', async () => {
    const chart = await openCashFlowBars([['2026-10-01', 1200, 0]])
    let column!: Column
    await waitFor(() => {
      ;[column] = columns(chart)
      expect(column.inflow).toBeDefined()
    })
    expect(column.outflow).toBeUndefined()

    const tooltip = await hoverColumn(chart, column)
    expect(within(tooltip).getByText(`${t('reports.outflow')}: $0.00`)).toBeInTheDocument()
  })

  it('shows a zero net flow in the tooltip for a break-even period', async () => {
    const chart = await openCashFlowBars([['2026-10-01', 2000, 2000]])
    let column!: Column
    await waitFor(() => {
      ;[column] = columns(chart)
      expect(column.inflow!.y).toBeCloseTo(zeroLine(chart))
    })

    const tooltip = await hoverColumn(chart, column)
    expect(within(tooltip).getByText(`${t('reports.netFlow')}: $0.00`)).toHaveStyle({ color: 'var(--muted-foreground)' })
  })

  it('leaves a month with no cash flow empty, without a bar or a tooltip', async () => {
    const chart = await openCashFlowBars([
      ['2026-10-01', 3000, 2500],
      ['2026-11-01', 0, 0],
      ['2026-12-01', 2500, 3000],
    ])
    let surplus!: Column
    await waitFor(() => {
      const drawn = columns(chart)
      expect(drawn.map(({ x, width }) => x + width / 2)).toEqual([
        periodX(chart, '2026-10-01'),
        periodX(chart, '2026-12-01'),
      ])
      ;[surplus] = drawn
    })

    const tooltip = await hoverColumn(chart, surplus)
    expect(tooltip).toHaveTextContent('2026-10-01')

    hoverAt(chart, periodX(chart, '2026-11-01'))
    await waitFor(() => expect(tooltip).toBeEmptyDOMElement())
  })

  it('draws no bars and no tooltip when the whole range has no cash flow', async () => {
    api.reports.cashFlow.mockResolvedValue(report('cash_flow', [
      { date: '2026-10-01', breakdowns: { inflow: 0, outflow: 0 } },
      { date: '2026-11-01', breakdowns: { inflow: 0, outflow: 0 } },
    ]))
    const container = await openTab('reports.cashFlow')
    // The balance projection is the tab's area chart; the other one holds the bars.
    const chart = await waitFor(() => {
      const found = Array.from(container.querySelectorAll<HTMLElement>('.recharts-wrapper'))
        .find((wrapper) => !wrapper.querySelector('.recharts-area') && wrapper.querySelector('.recharts-xAxis-tick-labels text'))
      expect(found).toBeDefined()
      return found!
    })

    expect(chart.querySelectorAll('.recharts-bar-rectangle path')).toHaveLength(0)
    const tooltip = hoverAt(chart, periodX(chart, '2026-10-01'))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(tooltip).toBeEmptyDOMElement()
  })

  it('masks every tooltip amount in privacy mode', async () => {
    privacy.on = true
    const chart = await openCashFlowBars([['2026-10-01', 3000, 2500]])
    let column!: Column
    await waitFor(() => {
      ;[column] = columns(chart)
      expect(column.outflow).toBeDefined()
    })

    const tooltip = await hoverColumn(chart, column)
    expect(within(tooltip).getByText(`${t('reports.inflow')}: ••••`)).toBeInTheDocument()
    expect(within(tooltip).getByText(`${t('reports.outflow')}: ••••`)).toBeInTheDocument()
    expect(within(tooltip).getByText(`${t('reports.netFlow')}: ••••`)).toBeInTheDocument()
    expect(tooltip).not.toHaveTextContent('$')
  })

  it('keeps the Net Worth bars stacked by sign, without a net flow row', async () => {
    api.reports.netWorth.mockResolvedValue(
      report('net_worth', [{ date: '2026-08-01', breakdowns: { accounts: 3000, liabilities: 2500 } }], ['accounts', 'liabilities']),
    )
    const chart = await barChart(await openTab())
    const segments = () => Array.from(chart.querySelectorAll('.recharts-bar-rectangle path'), box)
      .sort((a, b) => a.y - b.y)
    await waitFor(() => {
      const zero = zeroLine(chart)
      const [assets, debt] = segments()

      expect(assets.y + assets.height).toBeCloseTo(zero)
      expect(debt.y).toBeCloseTo(zero)
      expect(assets.height / debt.height).toBeCloseTo(3000 / 2500)
    })

    const path = chart.querySelector('.recharts-bar-rectangle path')!
    const tooltip = await hoverColumn(chart, {
      x: Number(path.getAttribute('x')),
      width: Number(path.getAttribute('width')),
    })
    expect(tooltip).toHaveTextContent(`${t('reports.accounts')}: $3,000.00`)
    expect(tooltip).toHaveTextContent(`${t('reports.liabilities')}: -$2,500.00`)
    expect(tooltip).not.toHaveTextContent(t('reports.netFlow'))
    expect(screen.queryByTestId('cash-flow-net')).not.toBeInTheDocument()
  })
})
