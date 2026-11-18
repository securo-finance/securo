import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
import { onlineManager } from '@tanstack/react-query'

import AssetsPage from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset } from '@/types'

const api = vi.hoisted(() => ({
  assets: {
    list: vi.fn(),
    portfolioTrend: vi.fn(),
    refreshPrices: vi.fn(),
  },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
  fxRates: { status: vi.fn() },
}))

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}))

const workspace = vi.hoisted(() => ({ canWrite: true, id: 'ws-1' as string }))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false }),
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { is_superuser: false, preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: workspace.canWrite, current: { id: workspace.id } }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeWalletIds: null }),
}))
vi.mock('@/lib/page-chat-context', () => ({
  useRegisterPageChatContext: () => {},
}))

function asset(lastPrice = 10): Asset {
  return {
    id: 'asset-1', user_id: 'user-1', name: 'Holding', type: 'stock', currency: 'USD',
    units: 10, valuation_method: 'market_price', purchase_date: null, purchase_price: null,
    sell_date: null, sell_price: null, growth_type: null, growth_rate: null,
    growth_frequency: null, growth_start_date: null, is_archived: false, position: 0,
    current_value: 100, current_value_primary: 100, gain_loss: null, gain_loss_primary: null,
    value_count: 0, source: 'manual', connection_id: null, isin: null, maturity_date: null,
    group_id: null, ticker: 'AAPL', ticker_exchange: null, last_price: lastPrice, last_price_at: null,
    logo_url: null, average_price: null, total_invested: null, realized_gain: null,
    transaction_count: 0,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  workspace.id = 'ws-1'
  api.assets.list.mockResolvedValue([asset()])
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

afterEach(() => {
  onlineManager.setOnline(true)
})

describe('Assets price refresh for the whole portfolio (issue #778)', () => {
  it('refreshes every market-priced asset with one click and reports the counts', async () => {
    api.assets.refreshPrices.mockResolvedValue({ refreshed: 3, skipped: 1, rate_limited: 0 })
    renderWithProviders(<AssetsPage />)

    const button = await screen.findByRole('button', { name: t('assets.refreshAllPrices') })
    button.click()

    // The selected workspace goes with the request, so the refresh never
    // lands in another one.
    await waitFor(() => expect(api.assets.refreshPrices).toHaveBeenCalledWith('ws-1'))
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        t('assets.refreshAllPricesDone', { refreshed: 3, skipped: 1 }),
      ),
    )
    // The list is refetched so the rows show the new prices.
    await waitFor(() => expect(api.assets.list).toHaveBeenCalledTimes(2))
  })

  it('shows the price the refresh wrote', async () => {
    // The second read is what the page gets after the refresh; if the row
    // ignored it the old price would stay on screen.
    api.assets.list
      .mockResolvedValueOnce([asset(10)])
      .mockResolvedValueOnce([asset(12)])
    api.assets.refreshPrices.mockResolvedValue({ refreshed: 1, skipped: 0, rate_limited: 0 })
    renderWithProviders(<AssetsPage />)

    expect(await screen.findByText('$10.00')).toBeInTheDocument()
    screen.getByRole('button', { name: t('assets.refreshAllPrices') }).click()

    expect(await screen.findByText('$12.00')).toBeInTheDocument()
    expect(screen.queryByText('$10.00')).not.toBeInTheDocument()
  })

  it('says there is nothing to refresh when the portfolio has no market-priced asset', async () => {
    api.assets.refreshPrices.mockResolvedValue({ refreshed: 0, skipped: 0, rate_limited: 0 })
    renderWithProviders(<AssetsPage />)

    const button = await screen.findByRole('button', { name: t('assets.refreshAllPrices') })
    button.click()

    await waitFor(() => expect(toast.info).toHaveBeenCalledWith(t('assets.refreshAllPricesNone')))
  })

  it('refetches the rows and says how many prices were written when the provider rate-limits', async () => {
    // A 429 can arrive after some prices were already written and committed,
    // so the list must be refetched on this path too instead of showing stale
    // values under a bare error.
    api.assets.list
      .mockResolvedValueOnce([asset(10)])
      .mockResolvedValueOnce([asset(11)])
    api.assets.refreshPrices.mockRejectedValue({
      response: {
        status: 429,
        data: {
          detail: {
            message: 'Market data provider is currently rate-limiting.',
            refreshed: 2,
            skipped: 1,
            rate_limited: 1,
          },
        },
      },
    })
    renderWithProviders(<AssetsPage />)

    expect(await screen.findByText('$10.00')).toBeInTheDocument()
    screen.getByRole('button', { name: t('assets.refreshAllPrices') }).click()

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        t('assets.refreshAllPricesRateLimited', { refreshed: 2, skipped: 1 }),
      ),
    )
    expect(await screen.findByText('$11.00')).toBeInTheDocument()
  })

  it('sends the refresh to the workspace that was selected when the user clicked', async () => {
    // A mutation the browser paused while offline resumes later. The real
    // request path has to carry the workspace the user clicked in, not the
    // one selected by then, so the header is checked on the wire instead of
    // trusting the mock's argument.
    const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
    const sent: Array<{ url: string; workspace: unknown }> = []
    actual.api.defaults.adapter = async (config) => {
      sent.push({ url: config.url ?? '', workspace: config.headers['X-Workspace-Id'] })
      return {
        data: { refreshed: 1, skipped: 0, rate_limited: 0 },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      }
    }
    api.assets.refreshPrices.mockImplementation((workspaceId: string | null) =>
      actual.assets.refreshPrices(workspaceId),
    )
    renderWithProviders(<AssetsPage />)

    expect(await screen.findByText('$10.00')).toBeInTheDocument()
    onlineManager.setOnline(false)
    await act(async () => {
      screen.getByRole('button', { name: t('assets.refreshAllPrices') }).click()
    })
    expect(sent).toHaveLength(0)

    // The switch lands while the request is paused: the interceptor would
    // stamp ws-2 on anything that did not name its own workspace.
    workspace.id = 'ws-2'
    localStorage.setItem(actual.WORKSPACE_STORAGE_KEY, 'ws-2')
    await act(async () => {
      onlineManager.setOnline(true)
    })

    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].url).toBe('/assets/refresh-prices')
    expect(sent[0].workspace).toBe('ws-1')
  })

  it('does not show the result in a workspace the user moved to', async () => {
    // The refresh belongs to the workspace it was asked in: switching while
    // the provider answers must not report its counts over another portfolio.
    let finish: ((value: { refreshed: number; skipped: number; rate_limited: number }) => void) | undefined
    api.assets.refreshPrices.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve
      }),
    )
    const { rerender } = renderWithProviders(<AssetsPage />)

    expect(await screen.findByText('$10.00')).toBeInTheDocument()
    screen.getByRole('button', { name: t('assets.refreshAllPrices') }).click()
    await waitFor(() => expect(api.assets.refreshPrices).toHaveBeenCalled())

    workspace.id = 'ws-2'
    rerender(<AssetsPage />)
    await act(async () => {
      finish?.({ refreshed: 3, skipped: 1, rate_limited: 0 })
    })

    expect(toast.success).not.toHaveBeenCalled()
    expect(api.assets.list).toHaveBeenCalledTimes(1)
  })

  it('does not offer the refresh to a viewer', async () => {
    workspace.canWrite = false
    renderWithProviders(<AssetsPage />)

    await screen.findByText('Holding')
    expect(screen.queryByRole('button', { name: t('assets.refreshAllPrices') })).not.toBeInTheDocument()
  })
})
