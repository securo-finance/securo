import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import AssetsPage from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset } from '@/types'

const api = vi.hoisted(() => ({
  assets: { list: vi.fn(), portfolioTrend: vi.fn(), delete: vi.fn() },
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
  useAuth: () => ({ user: { is_superuser: false, preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeWalletIds: null }),
}))
vi.mock('@/lib/page-chat-context', () => ({
  useRegisterPageChatContext: () => {},
}))

function asset(overrides: Partial<Asset>): Asset {
  return {
    id: 'asset-1',
    user_id: 'user-1',
    name: 'CDB Banco',
    type: 'other',
    currency: 'BRL',
    units: null,
    valuation_method: 'manual',
    purchase_date: null,
    purchase_price: null,
    sell_date: null,
    sell_price: null,
    growth_type: null,
    growth_rate: null,
    growth_frequency: null,
    growth_start_date: null,
    is_archived: false,
    position: 0,
    current_value: 1000,
    current_value_primary: 1000,
    gain_loss: null,
    gain_loss_primary: null,
    value_count: 0,
    source: 'manual',
    connection_id: null,
    isin: null,
    maturity_date: null,
    group_id: null,
    ticker: null,
    ticker_exchange: null,
    last_price: null,
    last_price_at: null,
    provider_value_date: null,
    logo_url: null,
    average_price: null,
    total_invested: null,
    realized_gain: null,
    transaction_count: 0,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assets.delete.mockResolvedValue(undefined)
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Deleting an asset a bank connection imported', () => {
  it('lets the user delete it and says a sync with asset import on brings it back', async () => {
    api.assets.list.mockResolvedValueOnce([asset({ source: 'pluggy', connection_id: 'conn-1' })]).mockResolvedValue([])
    const user = userEvent.setup()
    renderWithProviders(<AssetsPage />)

    await screen.findByText('CDB Banco')
    const deleteButton = screen.getByRole('button', { name: t('common.delete') })
    expect(deleteButton).toBeEnabled()
    await user.click(deleteButton)

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(t('assets.confirmDeleteSynced'))).toBeVisible()
    await user.click(within(dialog).getByRole('button', { name: t('common.delete') }))

    await waitFor(() => expect(api.assets.delete).toHaveBeenCalledWith('asset-1'))
    // Deleting finishes in the page, not only at the client: the confirmation
    // closes and the holding leaves the list once the list is refetched.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.queryByText('CDB Banco')).not.toBeInTheDocument())
  })

  it('does not warn about a sync for a manual asset', async () => {
    api.assets.list.mockResolvedValue([asset({})])
    const user = userEvent.setup()
    renderWithProviders(<AssetsPage />)

    await screen.findByText('CDB Banco')
    await user.click(screen.getByRole('button', { name: t('common.delete') }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(t('assets.confirmDelete'))).toBeVisible()
    expect(within(dialog).queryByText(t('assets.confirmDeleteSynced'))).not.toBeInTheDocument()
  })
})
