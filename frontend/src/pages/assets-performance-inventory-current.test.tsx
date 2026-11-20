import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { QueryClient } from '@tanstack/react-query'

import AssetsPage from '@/pages/assets'
import { readLastSelection, writeLastSelection } from '@/lib/portfolio-performance-utils'
import { renderWithProviders } from '@/test/utils'
import type { Asset, AssetGroup } from '@/types'

const api = vi.hoisted(() => ({
  assets: {
    list: vi.fn(),
    portfolioTrend: vi.fn(),
    performance: vi.fn(),
    benchmarkSearch: vi.fn(),
    tradeImport: vi.fn(),
    enableTradeImport: vi.fn(),
  },
  assetGroups: { list: vi.fn(), create: vi.fn() },
  currencies: { list: vi.fn() },
  fxRates: { status: vi.fn() },
  connections: { sync: vi.fn() },
  auth: { updateMe: vi.fn(), me: vi.fn() },
  info: { get: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false }),
}))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({
    user: { is_superuser: false, preferences: { currency_display: 'USD' } },
    updateUser: () => undefined,
  }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true, canManage: false, current: { id: 'workspace-a' } }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeWalletIds: null }),
}))
vi.mock('@/lib/page-chat-context', () => ({ useRegisterPageChatContext: () => {} }))

const wallet1 = { id: 'wallet-1', name: 'Brokerage', position: 0, color: '#000' } as AssetGroup
const wallet2 = { id: 'wallet-2', name: 'Crypto', position: 1, color: '#111' } as AssetGroup
const perf = {
  benchmark_symbol: null, benchmark_provider: null, period: '1y',
  start_date: '2026-01-07', end_date: '2026-09-29', portfolio_return: 1,
  benchmark_return: null, excess_return: null, benchmarks: [],
  points: [{ date: '2026-01-07', portfolio: 0, benchmark: null, benchmarks: {} },
           { date: '2026-09-29', portfolio: 1, benchmark: null, benchmarks: {} }],
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  localStorage.setItem('securo.assetsTab', 'performance')
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assets.list.mockResolvedValue([] as Asset[])
  api.assets.performance.mockResolvedValue(perf)
  api.assets.tradeImport.mockResolvedValue({ enabled: true, issues: [] })
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
  api.info.get.mockResolvedValue({ performance_benchmarks_enabled: false })
})


const appClient = () =>
  new QueryClient({
    defaultOptions: { queries: { staleTime: 1000 * 60 * 5, retry: 1, retryDelay: 1 } },
  })
const inventoryReads = () => api.assetGroups.list.mock.calls.filter((call) => call[0]).length

describe('performance inventory when the tab is opened again', () => {
  it('does not prune the remembered selection with the copy read before', async () => {
    let served = [wallet1]
    let failing = false
    api.assetGroups.list.mockImplementation((workspaceId?: string) =>
      failing && workspaceId ? Promise.reject(new Error('offline')) : Promise.resolve(served),
    )
    writeLastSelection('workspace-a', {
      scope: 'custom',
      walletIds: ['wallet-1'],
      assetIds: [],
      period: '1y',
    })
    const { user } = renderWithProviders(<AssetsPage />, { queryClient: appClient() })
    await waitFor(() => expect(api.assets.performance).toHaveBeenCalledTimes(1))
    await user.click(screen.getByRole('button', { name: 'Holdings' }))

    // Another browser tab creates wallet-2 and selects it on its Performance tab.
    served = [wallet1, wallet2]
    writeLastSelection('workspace-a', {
      scope: 'custom',
      walletIds: ['wallet-1', 'wallet-2'],
      assetIds: [],
      period: '1y',
    })
    failing = true
    const before = inventoryReads()
    await user.click(screen.getByRole('button', { name: 'Performance' }))
    await waitFor(() => expect(inventoryReads()).toBe(before + 2))

    // The read failed: say so, and keep wallet-2 rather than pruning it with
    // the inventory cached before it existed.
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(readLastSelection('workspace-a')?.walletIds).toEqual(['wallet-1', 'wallet-2'])

    failing = false
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() =>
      expect(api.assets.performance.mock.calls.at(-1)?.[3]).toEqual(['wallet-1', 'wallet-2']),
    )
    expect(readLastSelection('workspace-a')?.walletIds).toEqual(['wallet-1', 'wallet-2'])
  })
})
