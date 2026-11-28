import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset } from '@/types'

const api = vi.hoisted(() => ({
  assets: { list: vi.fn(), archivedCount: vi.fn(), portfolioTrend: vi.fn() },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
  fxRates: { status: vi.fn() },
}))
const collectionFilter = vi.hoisted(() => ({ activeWalletIds: null as string[] | null }))

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
  useCollectionFilter: () => collectionFilter,
}))
vi.mock('@/lib/page-chat-context', () => ({
  useRegisterPageChatContext: () => {},
}))

function holding(
  id: string,
  name: string,
  currentValue: number,
  isArchived: boolean,
  groupId: string | null = null,
): Asset {
  return {
    id, user_id: 'user-1', name, type: 'other', currency: 'USD',
    units: null, valuation_method: 'manual', purchase_date: null, purchase_price: null,
    sell_date: null, sell_price: null, growth_type: null, growth_rate: null,
    growth_frequency: null, growth_start_date: null, is_archived: isArchived, position: 0,
    current_value: currentValue, current_value_primary: null, gain_loss: null, gain_loss_primary: null,
    value_count: 0, source: 'manual', connection_id: null, isin: null, maturity_date: null,
    group_id: groupId, ticker: null, ticker_exchange: null, last_price: null, last_price_at: null,
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
  const byWallet: Record<string, number> = {}
  let ungrouped = 0
  let count = 0
  for (const a of holdings) {
    if (!a.is_archived) continue
    count += 1
    if (a.group_id) byWallet[a.group_id] = (byWallet[a.group_id] ?? 0) + 1
    else ungrouped += 1
  }
  api.assets.archivedCount.mockResolvedValue({ count, by_wallet: byWallet, ungrouped })
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  collectionFilter.activeWalletIds = null
  api.assets.archivedCount.mockResolvedValue({ count: 0, by_wallet: {}, ungrouped: 0 })
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Archived holdings on the Assets page', () => {
  it('hides the toggle and empty-state button when there are no archived holdings', async () => {
    served([ACTIVE])
    renderWithProviders(<AssetsPage />)

    await screen.findByText('Active Broker')
    await waitFor(() => expect(api.assets.archivedCount).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('checkbox', { name: t('assets.showArchived') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assets.showArchived') })).not.toBeInTheDocument()
  })

  it('hides the empty-state Show archived button when the portfolio is empty with no archived holdings', async () => {
    served([])
    renderWithProviders(<AssetsPage />)

    expect(await screen.findByText(t('assets.noAssets'))).toBeVisible()
    await waitFor(() => expect(api.assets.archivedCount).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('checkbox', { name: t('assets.showArchived') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assets.showArchived') })).not.toBeInTheDocument()
  })

  it('asks the API for archived holdings only when the toggle is ticked', async () => {
    served([ACTIVE, ARCHIVED])
    const { user } = renderWithProviders(<AssetsPage />)

    await screen.findByText('Active Broker')
    expect(screen.queryByText('Archived Broker')).not.toBeInTheDocument()
    expect(api.assets.list).toHaveBeenCalledWith(false)

    await user.click(await screen.findByRole('checkbox', { name: t('assets.showArchived') }))

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
    api.assets.archivedCount.mockResolvedValue({ count: 1, by_wallet: {}, ungrouped: 1 })
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

    await user.click(await screen.findByRole('button', { name: t('assets.showArchived') }))

    expect(await screen.findByText('Archived Broker')).toBeVisible()
    expect(screen.queryByText(t('assets.noAssets'))).not.toBeInTheDocument()
  })

  it('scopes toggle visibility to the active collection wallets', async () => {
    const activeInSelected = holding('a-1', 'Selected Active', 100, false, 'wallet-a')
    const archivedInOther = holding('a-2', 'Other Archived', 200, true, 'wallet-b')
    const archivedUngrouped = holding('a-3', 'Ungrouped Archived', 300, true, null)
    served([activeInSelected, archivedInOther, archivedUngrouped])
    api.assetGroups.list.mockResolvedValue([
      {
        id: 'wallet-a', user_id: 'user-1', name: 'Wallet A', icon: 'wallet', color: '#0EA5E9',
        position: 0, source: 'manual', connection_id: null, institution_name: null,
        asset_count: 1, current_value: 100, current_value_primary: 100,
      },
      {
        id: 'wallet-b', user_id: 'user-1', name: 'Wallet B', icon: 'wallet', color: '#10B981',
        position: 1, source: 'manual', connection_id: null, institution_name: null,
        asset_count: 0, current_value: 0, current_value_primary: 0,
      },
    ])

    collectionFilter.activeWalletIds = ['wallet-a']
    const { unmount } = renderWithProviders(<AssetsPage />)

    await screen.findByText('Selected Active')
    await waitFor(() => expect(api.assets.archivedCount).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('checkbox', { name: t('assets.showArchived') })).not.toBeInTheDocument()

    unmount()
    collectionFilter.activeWalletIds = ['wallet-b']
    renderWithProviders(<AssetsPage />)

    expect(await screen.findByRole('checkbox', { name: t('assets.showArchived') })).toBeVisible()
    expect(await screen.findByRole('button', { name: t('assets.showArchived') })).toBeVisible()
  })

  it('keeps the checkbox visible when showArchived is already enabled in localStorage even with zero archived holdings', async () => {
    localStorage.setItem('securo:assets:showArchived', '1')
    served([ACTIVE])
    const { user } = renderWithProviders(<AssetsPage />)

    await screen.findByText('Active Broker')
    await waitFor(() => expect(api.assets.archivedCount).toHaveBeenCalledTimes(1))
    const checkbox = screen.getByRole('checkbox', { name: t('assets.showArchived') })
    expect(checkbox).toBeChecked()

    await user.click(checkbox)
    expect(localStorage.getItem('securo:assets:showArchived')).toBe('0')
    expect(screen.queryByRole('checkbox', { name: t('assets.showArchived') })).not.toBeInTheDocument()
  })
})
