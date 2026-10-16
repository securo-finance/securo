import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import { PortfolioPerformance } from '@/components/portfolio-performance'
import { assets, info } from '@/lib/api'
import {
  readBenchmarkSelections,
  readLastSelection,
  writeBenchmarkSelections,
  writeLastSelection,
} from '@/lib/portfolio-performance-utils'
import { createTestQueryClient, i18n, renderWithProviders } from '@/test/utils'
import type {
  Asset,
  AssetGroup,
  BenchmarkMatch,
  PortfolioPerformance as PerformanceData,
} from '@/types'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: {} }, updateUser: vi.fn() }),
}))

vi.mock('@/lib/api', () => ({
  assets: { performance: vi.fn(), benchmarkSearch: vi.fn() },
  auth: { updateMe: vi.fn() },
  info: { get: vi.fn() },
}))

const workspaceId = 'performance-workspace'
const benchmarks: BenchmarkMatch[] = [
  { provider: 'yahoo', symbol: '^GSPC', name: 'S&P 500', exchange: 'SNP' },
  {
    provider: 'yahoo',
    symbol: '^IXIC',
    name: 'Nasdaq Composite',
    exchange: 'NASDAQ',
  },
  { provider: 'yahoo', symbol: '^DJI', name: 'Dow Jones', exchange: 'DJI' },
  { provider: 'yahoo', symbol: '^FTSE', name: 'FTSE 100', exchange: 'FTSE' },
  { provider: 'b3', symbol: 'IBOV', name: 'Ibovespa', exchange: 'B3' },
]
const performanceData: PerformanceData = {
  benchmark_symbol: '^GSPC',
  benchmark_provider: 'yahoo',
  period: '1y',
  start_date: '2026-01-07',
  end_date: '2026-09-29',
  portfolio_return: 12.5,
  benchmark_return: 8.25,
  excess_return: 4.25,
  // The API is allowed to return metrics in a different order to selection.
  benchmarks: [
    {
      key: 'yahoo:^IXIC',
      symbol: '^IXIC',
      provider: 'yahoo',
      benchmark_return: 15.75,
      excess_return: -3.25,
    },
    {
      key: 'yahoo:^GSPC',
      symbol: '^GSPC',
      provider: 'yahoo',
      benchmark_return: 8.25,
      excess_return: 4.25,
    },
  ],
  points: [
    {
      date: '2026-01-07',
      portfolio: 0,
      benchmark: 0,
      benchmarks: { 'yahoo:^GSPC': 0, 'yahoo:^IXIC': 0 },
    },
    {
      date: '2026-09-29',
      portfolio: 12.5,
      benchmark: 8.25,
      benchmarks: { 'yahoo:^GSPC': 8.25, 'yahoo:^IXIC': 15.75 },
    },
  ],
}

const holdings = [
  { id: 'apple', group_id: 'brokerage', name: 'Apple', ticker: 'AAPL' },
  { id: 'cash', group_id: null, name: 'Cash reserve', ticker: null },
] as Asset[]
const wallets = [{ id: 'brokerage', name: 'Brokerage', color: '#6366f1' }] as AssetGroup[]

function renderPerformance(
  options: {
    mask?: (value: string) => string
    assetGroupIds?: string[] | null
  } = {},
) {
  const queryClient = createTestQueryClient()
  queryClient.setQueryDefaults(['asset-performance'], { retryDelay: 0 })
  return renderWithProviders(
    <PortfolioPerformance
      workspaceId={workspaceId}
      assetGroupIds={options.assetGroupIds ?? null}
      holdings={holdings}
      wallets={wallets}
      locale="en-US"
      dateLocale="en-US"
      mask={options.mask ?? ((value) => value)}
    />,
    { queryClient },
  )
}

/** Point at the chart. jsdom has no layout, so the chart gets a size first. */
function hoverChart(container: HTMLElement, clientX: number) {
  const chart = container.querySelector<HTMLElement>('.recharts-wrapper')!
  vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue(
    DOMRect.fromRect({ width: 800, height: 300 }),
  )
  Object.defineProperty(chart, 'offsetWidth', { configurable: true, value: 800 })
  Object.defineProperty(chart, 'offsetHeight', { configurable: true, value: 300 })
  fireEvent.mouseMove(chart, { clientX, clientY: 150 })
}

describe('PortfolioPerformance', () => {
  beforeEach(async () => {
    vi.resetAllMocks()
    localStorage.clear()
    await i18n.changeLanguage('en')
    vi.mocked(assets.performance).mockResolvedValue(performanceData)
    vi.mocked(assets.benchmarkSearch).mockResolvedValue([benchmarks[4]])
    vi.mocked(info.get).mockResolvedValue({ features: { agents: false } })
  })

  it('hides benchmark comparison when the server has it disabled', async () => {
    vi.mocked(info.get).mockResolvedValue({
      features: { agents: false, performance_benchmarks: false },
    })
    writeBenchmarkSelections(workspaceId, benchmarks.slice(0, 2))
    renderPerformance()
    await screen.findByText('+12.50%')

    await waitFor(() => {
      expect(vi.mocked(assets.performance).mock.lastCall?.[0]).toEqual([])
    })
    expect(screen.queryByRole('button', { name: /Compare/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: 'Benchmarks' })).not.toBeInTheDocument()
    // The stored choice survives, so re-enabling brings it back.
    expect(readBenchmarkSelections(workspaceId)).toHaveLength(2)
  })

  it('makes an empty custom selection actionable and includes wallet children without counting them twice', async () => {
    writeLastSelection(workspaceId, {
      scope: 'custom',
      walletIds: [],
      assetIds: [],
      period: '1y',
    })
    const { user } = renderPerformance()

    expect(screen.getByRole('status')).toHaveTextContent('Select at least one wallet or asset')
    expect(assets.performance).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Select wallets and assets' }))

    const scope = screen.getByRole('dialog', { name: 'Portfolio scope' })
    const apple = within(scope).getByRole('checkbox', { name: 'Apple · AAPL' })
    const brokerage = within(scope).getByRole('checkbox', {
      name: 'Brokerage',
    })
    await user.click(apple)
    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith([], '1y', null, [], ['apple']),
    )

    await user.click(brokerage)
    expect(apple).toBeChecked()
    expect(apple).toBeDisabled()
    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith([], '1y', null, ['brokerage'], []),
    )
    expect(within(scope).getByText('1 selected')).toBeInTheDocument()

    await user.click(within(scope).getByRole('checkbox', { name: 'Cash reserve' }))
    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith([], '1y', null, ['brokerage'], ['cash']),
    )
    await user.click(brokerage)
    expect(apple).not.toBeChecked()
    expect(apple).toBeEnabled()
    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith([], '1y', null, [], ['cash']),
    )
  })

  it('updates the period by keyboard, preserves the collection filter, and remembers the choice', async () => {
    const { user } = renderPerformance({
      assetGroupIds: ['wallet-z', 'wallet-a'],
    })
    await screen.findByText('+12.50%')
    expect(screen.getByRole('button', { name: '1Y' })).toHaveAttribute('aria-pressed', 'true')

    const period = within(screen.getByRole('group', { name: 'Time period' })).getByRole('button', {
      name: '3M',
    })
    period.focus()
    await user.keyboard('{Enter}')

    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith(
        [],
        '3m',
        ['wallet-a', 'wallet-z'],
        undefined,
        undefined,
      ),
    )
    expect(period).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '1Y' })).toHaveAttribute('aria-pressed', 'false')
    expect(readLastSelection(workspaceId)?.period).toBe('3m')
  })

  it('supports keyboard benchmark selection and removal at the five-index limit', async () => {
    writeBenchmarkSelections(workspaceId, benchmarks.slice(0, 4))
    const { user } = renderPerformance()
    await screen.findByText('+12.50%')

    screen.getByRole('button', { name: /^Compare\s*4$/ }).focus()
    await user.keyboard('{Enter}')
    const dialog = screen.getByRole('dialog', { name: 'Benchmarks' })
    const search = within(dialog).getByRole('textbox', {
      name: 'Search an index by name or symbol…',
    })
    expect(search).toHaveFocus()
    await user.type(search, 'Ibov')
    const result = await within(dialog).findByRole('button', {
      name: /Ibovespa\s*IBOV · B3/,
    })
    await user.tab()
    for (let index = 0; document.activeElement !== result && index < 7; index += 1) await user.tab()
    expect(result).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(
      within(dialog).getByText('Up to five indices can be compared at once.'),
    ).toBeInTheDocument()
    expect(within(dialog).queryByRole('textbox')).not.toBeInTheDocument()
    expect(readBenchmarkSelections(workspaceId)).toEqual(benchmarks)
    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith(
        benchmarks,
        '1y',
        null,
        undefined,
        undefined,
      ),
    )

    const remove = within(dialog).getByRole('button', {
      name: 'Remove Ibovespa',
    })
    remove.focus()
    await user.keyboard('{Enter}')
    expect(within(dialog).getByRole('textbox')).toBeInTheDocument()
    expect(readBenchmarkSelections(workspaceId)).toEqual(benchmarks.slice(0, 4))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Benchmarks' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Compare\s*4$/ })).toHaveFocus()
  })

  it('shows actual coverage dates and matches each benchmark to its own return and excess', async () => {
    writeBenchmarkSelections(workspaceId, benchmarks.slice(0, 2))
    const { container } = renderPerformance()
    await screen.findByText('+12.50%')

    const dates = Array.from(container.querySelectorAll('time'))
    expect(dates.map((date) => date.dateTime)).toEqual(['2026-01-07', '2026-09-29'])
    expect(dates.map((date) => date.textContent)).toEqual(['Jan 7, 2026', 'Sep 29, 2026'])

    const comparisons = screen.getByRole('complementary', {
      name: 'Benchmarks',
    })
    const sp500Card = within(comparisons).getByText('+8.25%').parentElement!
    expect(within(sp500Card).getByText('S&P 500')).toBeInTheDocument()
    expect(within(sp500Card).getByText('+4.25 pp')).toBeInTheDocument()
    const nasdaqCard = within(comparisons).getByText('+15.75%').parentElement!
    expect(within(nasdaqCard).getByText('Nasdaq Composite')).toBeInTheDocument()
    expect(within(nasdaqCard).getByText('-3.25 pp')).toBeInTheDocument()
  })

  it('masks portfolio, benchmark, excess, and chart-axis returns in privacy mode', async () => {
    writeBenchmarkSelections(workspaceId, benchmarks.slice(0, 2))
    const mask = vi.fn(() => '••••')
    const { container } = renderPerformance({ mask })
    await screen.findByRole('complementary', { name: 'Benchmarks' })

    expect(screen.getAllByText('••••').length).toBeGreaterThanOrEqual(5)
    for (const value of ['+12.50%', '+8.25%', '+15.75%', '+4.25 pp', '-3.25 pp']) {
      expect(mask).toHaveBeenCalledWith(value)
      expect(container).not.toHaveTextContent(value)
    }
    await waitFor(() => {
      const ticks = container.querySelectorAll(
        '.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value',
      )
      expect(ticks.length).toBeGreaterThan(0)
      for (const tick of ticks) expect(tick).toHaveTextContent('••••')
    })
  })

  it('gives a chart that moves less than 1% distinct axis labels', async () => {
    vi.mocked(assets.performance).mockResolvedValue({
      ...performanceData,
      portfolio_return: 0.5,
      points: [
        { date: '2026-01-07', portfolio: 0, benchmark: 0, benchmarks: {} },
        { date: '2026-09-29', portfolio: 0.5, benchmark: 0, benchmarks: {} },
      ],
    })
    const { container } = renderPerformance()

    await waitFor(() => {
      const labels = [
        ...container.querySelectorAll(
          '.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value',
        ),
      ].map((tick) => tick.textContent)
      expect(labels.length).toBeGreaterThan(2)
      expect(new Set(labels).size).toBe(labels.length)
    })
  })

  it('shows a dash in the tooltip where an index has no value, not 0%', async () => {
    writeBenchmarkSelections(workspaceId, benchmarks.slice(0, 1))
    vi.mocked(assets.performance).mockResolvedValue({
      ...performanceData,
      points: [
        // The index's history starts after the period does.
        { date: '2026-01-07', portfolio: 0, benchmark: 0, benchmarks: {} },
        performanceData.points[1],
      ],
    })
    const { container } = renderPerformance()
    await waitFor(() => expect(container.querySelector('.recharts-line')).toBeInTheDocument())

    hoverChart(container, 90)

    const tooltip = await waitFor(() => {
      const content = container.querySelector<HTMLElement>('.recharts-tooltip-wrapper')
      expect(content).toHaveTextContent('Jan 7, 2026')
      return content!
    })
    expect(within(tooltip).getByText('S&P 500').nextElementSibling).toHaveTextContent('—')
  })

  it('offers a retry after a failed request and recovers without resetting the controls', async () => {
    vi.mocked(assets.performance).mockRejectedValue({
      response: {
        data: { detail: 'Historical prices are temporarily unavailable.' },
      },
    })
    const { user } = renderPerformance()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Historical prices are temporarily unavailable.',
    )

    vi.mocked(assets.performance).mockResolvedValue(performanceData)
    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('+12.50%')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '1Y' })).toHaveAttribute('aria-pressed', 'true')
  })

  it.each([
    ['unavailable', 'Error at the data source. Try again.'],
    ['rate_limited', 'The data source is limiting requests. Try again later.'],
    ['no_data', 'The data source returned no history for this period.'],
  ] as const)(
    'keeps portfolio and healthy comparisons visible for a %s source and supports retry',
    async (sourceError, message) => {
      writeBenchmarkSelections(workspaceId, benchmarks.slice(0, 2))
      vi.mocked(assets.performance).mockResolvedValue({
        ...performanceData,
        benchmark_return: null,
        excess_return: null,
        benchmarks: performanceData.benchmarks.map((metric) =>
          metric.key === 'yahoo:^GSPC'
            ? {
                ...metric,
                benchmark_return: null,
                excess_return: null,
                source_error: sourceError,
              }
            : metric,
        ),
        points: performanceData.points.map((point) => ({
          ...point,
          benchmark: 0,
          benchmarks: { 'yahoo:^IXIC': point.benchmarks['yahoo:^IXIC'] },
        })),
      })
      const { user, container } = renderPerformance()
      expect(await screen.findByRole('alert')).toHaveTextContent(message)
      expect(screen.getByText('+12.50%')).toBeInTheDocument()
      expect(screen.getByText('+15.75%')).toBeInTheDocument()
      expect(screen.queryByText('+8.25%')).not.toBeInTheDocument()
      expect(screen.queryByText('+4.25 pp')).not.toBeInTheDocument()
      await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(1))

      vi.mocked(assets.performance).mockResolvedValue(performanceData)
      await user.click(screen.getByRole('button', { name: 'Retry S&P 500' }))
      expect(await screen.findByText('+8.25%')).toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(readBenchmarkSelections(workspaceId)).toEqual(benchmarks.slice(0, 2))
      await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(2))
    },
  )

  it('keeps the portfolio chart visible when every benchmark source fails', async () => {
    writeBenchmarkSelections(workspaceId, benchmarks.slice(0, 2))
    vi.mocked(assets.performance).mockResolvedValue({
      ...performanceData,
      benchmark_return: null,
      excess_return: null,
      benchmarks: performanceData.benchmarks.map((metric) => ({
        ...metric,
        benchmark_return: null,
        excess_return: null,
        source_error: 'unavailable',
      })),
      points: performanceData.points.map((point) => ({
        ...point,
        benchmark: 0,
        benchmarks: {},
      })),
    })
    const { container } = renderPerformance()
    expect(await screen.findByText('+12.50%')).toBeInTheDocument()
    expect(screen.getAllByRole('alert')).toHaveLength(2)
    expect(screen.getAllByText('Error at the data source. Try again.')).toHaveLength(2)
    expect(container.querySelectorAll('.recharts-line')).toHaveLength(0)
    await waitFor(() => expect(container.querySelector('.recharts-area-curve')).toBeInTheDocument())
  })
})
