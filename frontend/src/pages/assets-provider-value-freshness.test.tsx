import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
import { localDateString } from '@/lib/date-utils'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset } from '@/types'

const api = vi.hoisted(() => ({
  assets: { list: vi.fn(), portfolioTrend: vi.fn() },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
  fxRates: { status: vi.fn() },
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
  useAuth: () => ({ user: { is_superuser: true, preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: false }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeWalletIds: null }),
}))
vi.mock('@/lib/page-chat-context', () => ({
  useRegisterPageChatContext: () => {},
}))

function syncedAsset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'asset-1', user_id: 'user-1', name: 'Frozen CDB', type: 'investment', currency: 'USD',
    units: null, valuation_method: 'manual', purchase_date: null, purchase_price: null,
    sell_date: null, sell_price: null, growth_type: null, growth_rate: null,
    growth_frequency: null, growth_start_date: null, is_archived: false, position: 0,
    current_value: 1000, current_value_primary: 1000, gain_loss: null, gain_loss_primary: null,
    value_count: 0, source: 'pluggy', connection_id: null, isin: null, maturity_date: null,
    group_id: null, ticker: null, ticker_exchange: null, last_price: null, last_price_at: null,
    logo_url: null, provider_value_date: null, average_price: null, total_invested: null,
    realized_gain: null, transaction_count: 0,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: true })
})

describe('Assets provider value freshness badge', () => {
  it('flags a synced open position whose provider value is over a week old', async () => {
    api.assets.list.mockResolvedValue([syncedAsset({ provider_value_date: '2026-06-17' })])
    renderWithProviders(<AssetsPage />)

    expect(
      await screen.findByText(t('assets.providerValueStale', { date: '6/17/2026' })),
    ).toBeVisible()
  })

  it('does not flag a synced position the provider updated today', async () => {
    api.assets.list.mockResolvedValue([syncedAsset({ provider_value_date: localDateString() })])
    renderWithProviders(<AssetsPage />)

    await screen.findByText('Frozen CDB')
    await waitFor(() =>
      expect(screen.queryByTitle(t('assets.providerValueStaleTitle'))).not.toBeInTheDocument(),
    )
  })

  it('does not flag a sold position even with an old provider value date', async () => {
    api.assets.list.mockResolvedValue([
      syncedAsset({ provider_value_date: '2026-06-17', sell_date: '2026-06-17' }),
    ])
    renderWithProviders(<AssetsPage />)

    expect(await screen.findByText(t('assets.sold'))).toBeVisible()
    expect(screen.queryByTitle(t('assets.providerValueStaleTitle'))).not.toBeInTheDocument()
  })

  it('does not flag a market-priced holding, whose value comes from its quote', async () => {
    api.assets.list.mockResolvedValue([
      syncedAsset({
        name: 'Apple',
        ticker: 'AAPL',
        valuation_method: 'market_price',
        units: 100,
        last_price: 10,
        provider_value_date: '2026-06-17',
      }),
    ])
    renderWithProviders(<AssetsPage />)

    expect(await screen.findByText('AAPL')).toBeVisible()
    expect(screen.queryByTitle(t('assets.providerValueStaleTitle'))).not.toBeInTheDocument()
  })
})
