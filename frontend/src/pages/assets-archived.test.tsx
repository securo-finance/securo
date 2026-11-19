import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
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
  useAuth: () => ({ user: { is_superuser: false, preferences: { currency_display: 'USD' } } }),
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

function holding(id: string, name: string, currentValue: number, isArchived: boolean): Asset {
  return {
    id, user_id: 'user-1', name, type: 'other', currency: 'USD',
    units: null, valuation_method: 'manual', purchase_date: null, purchase_price: null,
    sell_date: null, sell_price: null, growth_type: null, growth_rate: null,
    growth_frequency: null, growth_start_date: null, is_archived: isArchived, position: 0,
    current_value: currentValue, current_value_primary: null, gain_loss: null, gain_loss_primary: null,
    value_count: 0, source: 'manual', connection_id: null, isin: null, maturity_date: null,
    group_id: null, ticker: null, ticker_exchange: null, last_price: null, last_price_at: null,
    logo_url: null, provider_value_date: null, average_price: null, total_invested: null,
    realized_gain: null, transaction_count: 0,
  }
}

const ACTIVE = holding('asset-active', 'Active Broker', 100, false)
const ARCHIVED = holding('asset-archived', 'Archived Broker', 900, true)

/** The API leaves archived holdings out unless the caller asks for them. */
function served(holdings: Asset[]) {
  api.assets.list.mockImplementation((includeArchived: boolean) =>
    Promise.resolve(holdings.filter((a) => includeArchived || !a.is_archived)),
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Archived holdings on the Assets page', () => {
  it('asks the API for archived holdings only when the toggle is ticked', async () => {
    served([ACTIVE, ARCHIVED])
    const { user } = renderWithProviders(<AssetsPage />)

    await screen.findByText('Active Broker')
    expect(screen.queryByText('Archived Broker')).not.toBeInTheDocument()
    expect(api.assets.list).toHaveBeenCalledWith(false)

    await user.click(screen.getByRole('checkbox', { name: t('assets.showArchived') }))

    expect(await screen.findByText('Archived Broker')).toBeVisible()
    expect(screen.getByText(t('assets.archivedAssets'))).toBeVisible()
    expect(screen.getByText(t('assets.archivedNotCounted'))).toBeVisible()
    expect(api.assets.list).toHaveBeenLastCalledWith(true)
  })

  it('keeps archived holdings out of the portfolio total and share', async () => {
    // The toggle is remembered, so a reload comes back with them on (issue #1046).
    localStorage.setItem('securo:assets:showArchived', '1')
    served([ACTIVE, ARCHIVED])
    renderWithProviders(<AssetsPage />)

    const archivedRow = (await screen.findByText('Archived Broker')).closest('.grid') as HTMLElement
    expect(archivedRow).not.toBeNull()
    expect(api.assets.list).toHaveBeenCalledWith(true)
    // The active holding is 100% of a portfolio that does not count the 900.
    expect(screen.getByText('100.0%')).toBeVisible()
    expect(within(archivedRow).queryByText(/%$/)).not.toBeInTheDocument()
  })

  it('keeps the active holdings on screen when the archived request fails', async () => {
    let archivedFails = true
    api.assets.list.mockImplementation((includeArchived: boolean) => {
      if (!includeArchived) return Promise.resolve([ACTIVE])
      return archivedFails
        ? Promise.reject(new Error('index unreachable'))
        : Promise.resolve([ACTIVE, ARCHIVED])
    })
    localStorage.setItem('securo:assets:showArchived', '1')
    const { user } = renderWithProviders(<AssetsPage />)

    expect(await screen.findByText('Active Broker')).toBeVisible()
    expect(await screen.findByText(t('assets.archivedLoadFailed'))).toBeVisible()
    expect(screen.queryByText(t('assets.noAssets'))).not.toBeInTheDocument()

    // Retrying is the way out, not reloading the page.
    archivedFails = false
    await user.click(screen.getByRole('button', { name: t('common.retry') }))

    expect(await screen.findByText('Archived Broker')).toBeVisible()
    await waitFor(() => expect(screen.queryByText(t('assets.archivedLoadFailed'))).not.toBeInTheDocument())
  })

  it('offers the toggle when every holding is archived', async () => {
    served([ARCHIVED])
    const { user } = renderWithProviders(<AssetsPage />)

    expect(await screen.findByText(t('assets.noAssets'))).toBeVisible()

    await user.click(screen.getByRole('button', { name: t('assets.showArchived') }))

    expect(await screen.findByText('Archived Broker')).toBeVisible()
    expect(screen.queryByText(t('assets.noAssets'))).not.toBeInTheDocument()
  })
})
