import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
import { todayInTimezone } from '@/lib/date-utils'
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
const timezone = vi.hoisted(() => ({ value: 'UTC' as string | undefined }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => timezone.value }))
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

// The page compares the value date against the effective timezone's day, which
// is the mocked UTC. Fixtures are placed relative to that day so the age being
// tested never depends on when the suite runs.
function daysAgoInUtc(days: number) {
  const date = new Date(`${todayInTimezone('UTC')}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() - days)
  return date.toISOString().slice(0, 10)
}

function displayedDate(isoDate: string) {
  return new Date(`${isoDate}T00:00:00`).toLocaleDateString('en-US')
}

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

// What the user reads, plus the tooltip behind it: a badge that lost either
// half would still be a warning, and a test that only looked for one of them
// would miss it.
function expectNoStaleWarning() {
  expect(screen.queryByText(/not updated since/i)).not.toBeInTheDocument()
  expect(screen.queryByTitle(t('assets.providerValueStaleTitle'))).not.toBeInTheDocument()
}

beforeEach(() => {
  vi.clearAllMocks()
  timezone.value = 'UTC'
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: true })
})

describe('Assets provider value freshness badge', () => {
  it('flags a synced open position whose provider value is over a week old', async () => {
    const valueDate = daysAgoInUtc(10)
    api.assets.list.mockResolvedValue([syncedAsset({ provider_value_date: valueDate })])
    renderWithProviders(<AssetsPage />)

    expect(
      await screen.findByText(t('assets.providerValueStale', { date: displayedDate(valueDate) })),
    ).toBeVisible()
  })

  it('does not flag a synced position the provider updated today', async () => {
    api.assets.list.mockResolvedValue([
      syncedAsset({ provider_value_date: todayInTimezone('UTC') }),
    ])
    renderWithProviders(<AssetsPage />)

    await screen.findByText('Frozen CDB')
    await waitFor(expectNoStaleWarning)
  })

  it('does not flag a position exactly at the seven day boundary', async () => {
    api.assets.list.mockResolvedValue([
      syncedAsset({ provider_value_date: daysAgoInUtc(7) }),
    ])
    renderWithProviders(<AssetsPage />)

    await screen.findByText('Frozen CDB')
    await waitFor(expectNoStaleWarning)
  })

  it('does not flag a sold position even with an old provider value date', async () => {
    api.assets.list.mockResolvedValue([
      syncedAsset({
        provider_value_date: daysAgoInUtc(10),
        sell_date: todayInTimezone('UTC'),
      }),
    ])
    renderWithProviders(<AssetsPage />)

    expect(await screen.findByText(t('assets.sold'))).toBeVisible()
    expectNoStaleWarning()
  })

  it('waits for the effective timezone before comparing days', async () => {
    timezone.value = undefined
    api.assets.list.mockResolvedValue([
      syncedAsset({ provider_value_date: daysAgoInUtc(10) }),
    ])
    renderWithProviders(<AssetsPage />)

    await screen.findByText('Frozen CDB')
    expectNoStaleWarning()
  })
})
