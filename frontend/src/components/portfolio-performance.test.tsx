import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'

import { PortfolioPerformance, type PerformanceInventory } from '@/components/portfolio-performance'
import { assets, auth, connections, info } from '@/lib/api'
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
  User,
} from '@/types'

const authState = vi.hoisted(() => ({
  user: { preferences: {} } as { preferences: Record<string, unknown> },
  updateUser: (() => undefined) as (user: unknown) => void,
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => authState,
}))

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
}))
vi.mock('sonner', () => ({ toast: toastMock }))

vi.mock('@/lib/api', () => ({
  assets: {
    performance: vi.fn(),
    benchmarkSearch: vi.fn(),
    tradeImport: vi.fn(),
    enableTradeImport: vi.fn(),
  },
  connections: { sync: vi.fn() },
  auth: { updateMe: vi.fn(), me: vi.fn() },
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
      start_date: '2026-01-07',
    },
    {
      key: 'yahoo:^GSPC',
      symbol: '^GSPC',
      provider: 'yahoo',
      benchmark_return: 8.25,
      excess_return: 4.25,
      start_date: '2026-01-07',
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
    holdings?: Asset[]
    wallets?: AssetGroup[]
    inventory?: PerformanceInventory | null
    inventoryError?: boolean
    onRetryInventory?: () => void
    canManage?: boolean
  } = {},
) {
  const queryClient = createTestQueryClient()
  queryClient.setQueryDefaults(['asset-performance'], { retryDelay: 0 })
  const shownHoldings = options.holdings ?? holdings
  const shownWallets = options.wallets ?? wallets
  const element = (inventory: PerformanceInventory | null) => (
    <PortfolioPerformance
      workspaceId={workspaceId}
      assetGroupIds={options.assetGroupIds ?? null}
      holdings={shownHoldings}
      wallets={shownWallets}
      inventory={inventory}
      inventoryError={options.inventoryError}
      onRetryInventory={options.onRetryInventory}
      locale="en-US"
      dateLocale="en-US"
      mask={options.mask ?? ((value) => value)}
      canManage={options.canManage}
    />
  )
  const result = renderWithProviders(
    element(
      options.inventory !== undefined
        ? options.inventory
        : {
            walletIds: shownWallets.map((wallet) => wallet.id),
            assetIds: shownHoldings.map((holding) => holding.id),
          },
    ),
    { queryClient },
  )
  return {
    ...result,
    loadInventory: (inventory: PerformanceInventory) => result.rerender(element(inventory)),
  }
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
    authState.user = { preferences: {} }
    authState.updateUser = () => undefined
  })

  it('lets only an owner turn on trade import', async () => {
    vi.mocked(assets.tradeImport).mockResolvedValue({
      available: true,
      enabled: false,
      issues: [],
    })
    vi.mocked(assets.enableTradeImport).mockResolvedValue({
      available: true,
      enabled: true,
      issues: [],
    })
    const { user, unmount } = renderPerformance({ canManage: true })

    await user.click(await screen.findByRole('button', { name: 'Import trades' }))

    // The request names the workspace it was clicked in.
    expect(assets.enableTradeImport).toHaveBeenCalledTimes(1)
    expect(assets.enableTradeImport).toHaveBeenCalledWith(workspaceId)
    expect(assets.tradeImport).toHaveBeenCalledWith(workspaceId)
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Import trades' })).not.toBeInTheDocument()
    })
    unmount()

    renderPerformance()
    await screen.findByText('+12.50%')
    expect(assets.tradeImport).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Import trades' })).not.toBeInTheDocument()
  })

  it('lists holdings whose trades could not be imported and retries their connections', async () => {
    const issue = (
      connection_id: string,
      asset_id: string,
      name: string,
      status: 'incomplete' | 'no_history',
    ) => ({ connection_id, asset_id, name, ticker: null, status })
    vi.mocked(assets.tradeImport)
      .mockResolvedValueOnce({
        available: true,
        enabled: true,
        issues: [
          issue('connection-a', 'asset-a', 'Carbon Fund', 'incomplete'),
          issue('connection-b', 'asset-b', 'Global ETF', 'no_history'),
          issue('connection-a', 'asset-c', 'World ETF', 'no_history'),
        ],
      })
      .mockResolvedValue({ available: true, enabled: true, issues: [] })
    vi.mocked(connections.sync).mockResolvedValue({ sync_outcome: 'refreshed' } as never)
    const { user } = renderPerformance({ canManage: true })

    expect(await screen.findByText('Carbon Fund')).toBeInTheDocument()
    expect(screen.getByText('Global ETF').parentElement).toHaveTextContent(
      'Global ETF · No history from the bank',
    )
    expect(screen.getByText('Carbon Fund').parentElement).toHaveTextContent(
      'Carbon Fund · Incomplete history',
    )

    await user.click(screen.getByRole('button', { name: 'Try again' }))

    // Each affected connection is synced once, asking for the histories again.
    await waitFor(() => {
      expect(vi.mocked(connections.sync).mock.calls).toEqual([
        ['connection-a', { retryTradeImport: true, workspaceId }],
        ['connection-b', { retryTradeImport: true, workspaceId }],
      ])
    })
    await waitFor(() => {
      expect(screen.queryByText('Carbon Fund')).not.toBeInTheDocument()
    })
  })

  it('reports a trade-import retry that only partly synced as partial, not done', async () => {
    vi.mocked(assets.tradeImport).mockResolvedValue({
      available: true,
      enabled: true,
      issues: [
        {
          connection_id: 'connection-a',
          asset_id: 'asset-a',
          name: 'Carbon Fund',
          ticker: null,
          status: 'incomplete',
        },
      ],
    })
    vi.mocked(connections.sync).mockResolvedValue({ sync_outcome: 'partial' } as never)
    const { user } = renderPerformance({ canManage: true })

    await screen.findByText('Carbon Fund')
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    await waitFor(() => expect(toastMock.warning).toHaveBeenCalledTimes(1))
    expect(toastMock.warning).toHaveBeenCalledWith(i18n.t('accounts.syncPartial'))
    expect(toastMock.success).not.toHaveBeenCalled()
    // The issue list is asked for again, so what still needs attention shows.
    await waitFor(() => expect(assets.tradeImport).toHaveBeenCalledTimes(2))
  })

  it('does not list import problems to someone who cannot manage the workspace', async () => {
    renderPerformance()
    await screen.findByText('+12.50%')

    expect(assets.tradeImport).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('hides benchmark comparison when the server has it disabled', async () => {
    // /info answers after the component mounts: nothing may be requested
    // with the stored benchmarks while it is loading.
    let answerInfo!: (value: Awaited<ReturnType<typeof info.get>>) => void
    vi.mocked(info.get).mockReturnValue(
      new Promise((resolve) => {
        answerInfo = resolve
      }),
    )
    writeBenchmarkSelections(workspaceId, benchmarks.slice(0, 2))
    renderPerformance()
    expect(screen.getByRole('status', { name: 'Loading...' })).toBeInTheDocument()
    await waitFor(() => expect(info.get).toHaveBeenCalled())
    expect(assets.performance).not.toHaveBeenCalled()

    await act(async () => {
      answerInfo({ features: { agents: false, performance_benchmarks: false } })
    })
    await screen.findByText('+12.50%')

    expect(assets.performance).toHaveBeenCalled()
    for (const call of vi.mocked(assets.performance).mock.calls) expect(call[0]).toEqual([])
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
      expect(assets.performance).toHaveBeenLastCalledWith([], '1y', null, [], ['apple'], workspaceId),
    )

    await user.click(brokerage)
    expect(apple).toBeChecked()
    expect(apple).toBeDisabled()
    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith([], '1y', null, ['brokerage'], [], workspaceId),
    )
    expect(within(scope).getByText('1 selected')).toBeInTheDocument()

    await user.click(within(scope).getByRole('checkbox', { name: 'Cash reserve' }))
    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith([], '1y', null, ['brokerage'], ['cash'], workspaceId),
    )
    await user.click(brokerage)
    expect(apple).not.toBeChecked()
    expect(apple).toBeEnabled()
    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith([], '1y', null, [], ['cash'], workspaceId),
    )
  })

  it('lets a sold holding be measured on its own', async () => {
    writeLastSelection(workspaceId, {
      scope: 'custom',
      walletIds: [],
      assetIds: [],
      period: '1y',
    })
    const sold = {
      id: 'tesla',
      group_id: 'brokerage',
      name: 'Tesla',
      ticker: 'TSLA',
      sell_date: '2026-08-01',
    } as Asset
    const { user } = renderPerformance({ holdings: [...holdings, sold] })

    await user.click(screen.getByRole('button', { name: 'Select wallets and assets' }))
    const scope = screen.getByRole('dialog', { name: 'Portfolio scope' })
    await user.click(within(scope).getByRole('checkbox', { name: 'Tesla · TSLA · Sold' }))
    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith([], '1y', null, [], ['tesla'], workspaceId),
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
        workspaceId,
      ),
    )
    expect(period).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '1Y' })).toHaveAttribute('aria-pressed', 'false')
    expect(readLastSelection(workspaceId)?.period).toBe('3m')
  })

  it('supports keyboard benchmark selection and removal at the five-benchmark limit', async () => {
    writeBenchmarkSelections(workspaceId, benchmarks.slice(0, 4))
    const { user } = renderPerformance()
    await screen.findByText('+12.50%')

    screen.getByRole('button', { name: /^Compare\s*4$/ }).focus()
    await user.keyboard('{Enter}')
    const dialog = screen.getByRole('dialog', { name: 'Benchmarks' })
    const search = within(dialog).getByRole('textbox', {
      name: 'Search an index, ETF, stock or crypto by name or symbol…',
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
      within(dialog).getByText('Up to five benchmarks can be compared at once.'),
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
        workspaceId,
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
  it('labels the periods in the current language', async () => {
    await i18n.changeLanguage('pt-BR')
    renderPerformance()
    const periods = screen.getByRole('group', { name: 'Período' })
    expect(within(periods).getAllByRole('button').map((button) => button.textContent)).toEqual([
      '3M',
      '6M',
      'YTD',
      '1A',
      '3A',
      '5A',
    ])
  })

  it('keeps saved-view members the collection hides, drops deleted ones, and saves onto fresh preferences', async () => {
    const view = {
      id: 'view-1',
      name: 'Long term',
      wallet_ids: ['brokerage', 'hidden-wallet', 'deleted-wallet'],
      asset_ids: ['cash', 'archived-asset', 'deleted-asset'],
    }
    authState.user = { preferences: { performance_views: { [workspaceId]: [view] } } }
    // Another tab has since saved a setting and a second view.
    const otherTabView = { id: 'view-2', name: 'Other tab', wallet_ids: ['brokerage'], asset_ids: [] }
    vi.mocked(auth.me).mockResolvedValue({
      preferences: {
        language: 'en',
        performance_views: { [workspaceId]: [view, otherTabView], 'other-workspace': [] },
      },
    } as unknown as User)
    vi.mocked(auth.updateMe).mockImplementation(async (updates) => updates as User)
    const { user } = renderPerformance({
      // The active collection shows only the brokerage wallet.
      wallets,
      inventory: {
        walletIds: ['brokerage', 'hidden-wallet'],
        assetIds: ['apple', 'cash', 'archived-asset'],
      },
    })
    await screen.findByText('+12.50%')

    await user.click(screen.getByRole('button', { name: /Entire portfolio/ }))
    await user.click(
      within(screen.getByRole('dialog', { name: 'Portfolio scope' })).getByRole('button', {
        name: 'Long term',
      }),
    )
    await waitFor(() =>
      expect(assets.performance).toHaveBeenLastCalledWith(
        [],
        '1y',
        null,
        ['brokerage', 'hidden-wallet'],
        ['archived-asset', 'cash'],
        workspaceId,
      ),
    )
    // The view still matches: nothing to update yet.
    expect(screen.getByRole('button', { name: /Long term\s*4/ })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Long term/ }))
    const scope = screen.getByRole('dialog', { name: 'Portfolio scope' })
    await user.click(within(scope).getByRole('checkbox', { name: 'Cash reserve' }))
    await user.click(within(scope).getByRole('button', { name: 'Update “Long term”' }))

    await waitFor(() => expect(auth.updateMe).toHaveBeenCalledTimes(1))
    expect(vi.mocked(auth.me).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(auth.updateMe).mock.invocationCallOrder[0],
    )
    expect(vi.mocked(auth.updateMe).mock.calls[0][0]).toEqual({
      preferences: {
        language: 'en',
        performance_views: {
          'other-workspace': [],
          [workspaceId]: [
            { ...view, wallet_ids: ['brokerage', 'hidden-wallet'], asset_ids: ['archived-asset'] },
            otherTabView,
          ],
        },
      },
    })
  })

  it('says so when the view being updated was deleted in another tab, and keeps the selection', async () => {
    const view = { id: 'view-1', name: 'Long term', wallet_ids: ['brokerage'], asset_ids: [] }
    authState.user = { preferences: { performance_views: { [workspaceId]: [view] } } }
    const updateUser = vi.fn()
    authState.updateUser = updateUser
    // Another tab has deleted the view since this one loaded.
    const fresh = { preferences: { performance_views: { [workspaceId]: [] } } }
    vi.mocked(auth.me).mockResolvedValue(fresh as unknown as User)
    const { user } = renderPerformance()
    await screen.findByText('+12.50%')

    await user.click(screen.getByRole('button', { name: /Entire portfolio/ }))
    await user.click(
      within(screen.getByRole('dialog', { name: 'Portfolio scope' })).getByRole('button', {
        name: 'Long term',
      }),
    )
    await user.click(screen.getByRole('button', { name: /Long term/ }))
    const scope = screen.getByRole('dialog', { name: 'Portfolio scope' })
    await user.click(within(scope).getByRole('checkbox', { name: 'Cash reserve' }))
    await user.click(within(scope).getByRole('button', { name: 'Update “Long term”' }))

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        i18n.t('assets.performanceViewGone', { name: 'Long term' }),
      ),
    )
    expect(auth.updateMe).not.toHaveBeenCalled()
    expect(toastMock.success).not.toHaveBeenCalled()
    // This tab catches up with the deletion.
    expect(updateUser).toHaveBeenCalledWith(fresh)
    // The selection stays and can be saved as a new view.
    expect(within(scope).getByRole('checkbox', { name: 'Brokerage' })).toBeChecked()
    expect(within(scope).getByRole('checkbox', { name: 'Cash reserve' })).toBeChecked()
    expect(
      within(scope).queryByRole('button', { name: 'Update “Long term”' }),
    ).not.toBeInTheDocument()
    expect(
      within(scope).getByRole('textbox', { name: i18n.t('assets.performanceViewNamePlaceholder') }),
    ).toBeInTheDocument()
  })

  it('offers a retry when the inventory fails to load for a remembered custom selection', async () => {
    writeLastSelection(workspaceId, {
      scope: 'custom',
      walletIds: ['brokerage'],
      assetIds: ['cash'],
      period: '1y',
    })
    const onRetryInventory = vi.fn()
    const { user } = renderPerformance({ inventory: null, inventoryError: true, onRetryInventory })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(i18n.t('assets.performanceLoadError'))
    expect(screen.queryByRole('status', { name: 'Loading...' })).not.toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: i18n.t('common.retry') }))
    expect(onRetryInventory).toHaveBeenCalledTimes(1)
    // Without the inventory, the custom selection is not sent.
    expect(assets.performance).not.toHaveBeenCalled()
  })

  it('leaves deleted wallets and holdings out of a restored selection once the inventory loads', async () => {
    writeLastSelection(workspaceId, {
      scope: 'custom',
      walletIds: ['brokerage', 'deleted-wallet'],
      assetIds: ['cash', 'deleted-asset'],
      period: '1y',
    })
    const { loadInventory } = renderPerformance({ inventory: null })
    expect(screen.getByRole('status', { name: 'Loading...' })).toBeInTheDocument()
    await waitFor(() => expect(info.get).toHaveBeenCalled())
    expect(assets.performance).not.toHaveBeenCalled()

    loadInventory({ walletIds: ['brokerage'], assetIds: ['apple', 'cash'] })
    await screen.findByText('+12.50%')

    expect(vi.mocked(assets.performance).mock.calls).toEqual([
      [[], '1y', null, ['brokerage'], ['cash'], workspaceId],
    ])
    expect(screen.getByRole('button', { name: /Wallets or assets\s*2/ })).toBeInTheDocument()
    expect(readLastSelection(workspaceId)).toMatchObject({
      walletIds: ['brokerage'],
      assetIds: ['cash'],
    })
  })

  it('measures a period from the keyboard', async () => {
    vi.mocked(assets.performance).mockResolvedValue({
      ...performanceData,
      points: [
        { date: '2026-01-07', portfolio: 0, benchmark: null, benchmarks: {} },
        { date: '2026-05-04', portfolio: 10, benchmark: null, benchmarks: {} },
        { date: '2026-09-29', portfolio: 12.5, benchmark: null, benchmarks: {} },
      ],
    })
    const { container } = renderPerformance()
    await screen.findByText('+12.50%')

    const chart = await waitFor(() => {
      const surface = container.querySelector<SVGElement>('.recharts-surface')
      expect(surface).toBeInTheDocument()
      return surface!
    })
    expect(chart).toHaveAccessibleName('Portfolio performance chart')
    expect(chart).toHaveAccessibleDescription(/Enter or Space to mark the start/)

    await markRange(container, chart, 1)
    // The headline now shows the marked period's return, Jan 7 to May 4.
    expect(screen.getByText('Selected period').nextElementSibling).toHaveTextContent('+10.00%')
    expect(Array.from(container.querySelectorAll('time'), (date) => date.dateTime)).toEqual([
      '2026-01-07',
      '2026-05-04',
    ])

    fireEvent.keyDown(chart, { key: 'Escape' })
    expect(screen.queryByText('Selected period')).not.toBeInTheDocument()
    expect(screen.getByText('+12.50%')).toBeInTheDocument()
  })

  it('notes a benchmark that starts mid-series and shows it as unavailable before then', async () => {
    writeBenchmarkSelections(workspaceId, benchmarks.slice(0, 2))
    vi.mocked(assets.performance).mockResolvedValue({
      ...performanceData,
      benchmarks: performanceData.benchmarks.map((metric) =>
        metric.key === 'yahoo:^IXIC' ? { ...metric, start_date: '2026-05-04' } : metric,
      ),
      points: [
        { date: '2026-01-07', portfolio: 0, benchmark: 0, benchmarks: { 'yahoo:^GSPC': 0 } },
        {
          date: '2026-05-04',
          portfolio: 10,
          benchmark: 2,
          benchmarks: { 'yahoo:^GSPC': 2, 'yahoo:^IXIC': 0 },
        },
        {
          date: '2026-09-29',
          portfolio: 12.5,
          benchmark: 8.25,
          benchmarks: { 'yahoo:^GSPC': 8.25, 'yahoo:^IXIC': 15.75 },
        },
      ],
    })
    const { container } = renderPerformance()
    const comparisons = await screen.findByRole('complementary', { name: 'Benchmarks' })

    const nasdaqCard = within(comparisons).getByText('+15.75%').parentElement!
    expect(within(nasdaqCard).getByText('from May 4, 2026')).toBeInTheDocument()
    const sp500Card = within(comparisons).getByText('+8.25%').parentElement!
    expect(within(sp500Card).queryByText(/^from/)).not.toBeInTheDocument()
    expect(container).toHaveTextContent('Nasdaq Composite · from May 4, 2026')

    // Jan 7 to May 4: the Nasdaq has no value at the start.
    const chart = container.querySelector<SVGElement>('.recharts-surface')!
    await markRange(container, chart, 1)
    expect(within(sp500Card).getByText('+2.00%')).toBeInTheDocument()
    expect(within(sp500Card).getByText('+8.00 pp')).toBeInTheDocument()
    expect(within(nasdaqCard).getAllByText('—')).toHaveLength(2)
    expect(container).not.toHaveTextContent('NaN')
  })
})

/**
 * Focus the chart (its first date becomes active), mark the start with
 * Enter, move right `steps` dates and mark the end with Space.
 */
async function markRange(container: HTMLElement, chart: SVGElement, steps: number) {
  const tooltipDate = async (text: string) =>
    waitFor(() =>
      expect(container.querySelector('.recharts-tooltip-wrapper')).toHaveTextContent(text),
    )
  act(() => chart.focus())
  await tooltipDate('Jan 7, 2026')
  fireEvent.keyDown(chart, { key: 'Enter' })
  await waitFor(() => expect(screen.getByText(/Period start marked at Jan 7, 2026/)).toBeInTheDocument())
  for (let step = 0; step < steps; step += 1) fireEvent.keyDown(chart, { key: 'ArrowRight' })
  await tooltipDate('May 4, 2026')
  fireEvent.keyDown(chart, { key: ' ' })
  await screen.findByText('Selected period')
}
