import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

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

const workspace = vi.hoisted(() => ({ canWrite: true }))

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
  useWorkspace: () => ({ canWrite: workspace.canWrite }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeWalletIds: null }),
}))
vi.mock('@/lib/page-chat-context', () => ({
  useRegisterPageChatContext: () => {},
}))

function asset(): Asset {
  return {
    id: 'asset-1', user_id: 'user-1', name: 'Holding', type: 'stock', currency: 'USD',
    units: 10, valuation_method: 'market_price', purchase_date: null, purchase_price: null,
    sell_date: null, sell_price: null, growth_type: null, growth_rate: null,
    growth_frequency: null, growth_start_date: null, is_archived: false, position: 0,
    current_value: 100, current_value_primary: 100, gain_loss: null, gain_loss_primary: null,
    value_count: 0, source: 'manual', connection_id: null, isin: null, maturity_date: null,
    group_id: null, ticker: 'AAPL', ticker_exchange: null, last_price: 10, last_price_at: null,
    logo_url: null, average_price: null, total_invested: null, realized_gain: null,
    transaction_count: 0,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  api.assets.list.mockResolvedValue([asset()])
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Assets price refresh for the whole portfolio (issue #778)', () => {
  it('refreshes every market-priced asset with one click and reports the counts', async () => {
    api.assets.refreshPrices.mockResolvedValue({ refreshed: 3, skipped: 1, rate_limited: 0 })
    renderWithProviders(<AssetsPage />)

    const button = await screen.findByRole('button', { name: t('assets.refreshAllPrices') })
    button.click()

    await waitFor(() => expect(api.assets.refreshPrices).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        t('assets.refreshAllPricesDone', { refreshed: 3, skipped: 1 }),
      ),
    )
    // The list is refetched so the rows show the new prices.
    await waitFor(() => expect(api.assets.list).toHaveBeenCalledTimes(2))
  })

  it('says there is nothing to refresh when the portfolio has no market-priced asset', async () => {
    api.assets.refreshPrices.mockResolvedValue({ refreshed: 0, skipped: 0, rate_limited: 0 })
    renderWithProviders(<AssetsPage />)

    const button = await screen.findByRole('button', { name: t('assets.refreshAllPrices') })
    button.click()

    await waitFor(() => expect(toast.info).toHaveBeenCalledWith(t('assets.refreshAllPricesNone')))
  })

  it('does not offer the refresh to a viewer', async () => {
    workspace.canWrite = false
    renderWithProviders(<AssetsPage />)

    await screen.findByText('Holding')
    expect(screen.queryByRole('button', { name: t('assets.refreshAllPrices') })).not.toBeInTheDocument()
  })
})
