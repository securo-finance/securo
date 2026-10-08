import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset } from '@/types'

// Every API call the page (and its tabs) may make resolves to an empty list,
// except the ones a test sets explicitly.
const api = vi.hoisted(() => {
  const anyCall = () =>
    new Proxy({} as Record<string, ReturnType<typeof vi.fn>>, {
      get: (target, key: string) => (target[key] ??= vi.fn().mockResolvedValue([])),
    })
  return { assets: anyCall(), assetGroups: anyCall(), currencies: anyCall() }
})

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '•••' }),
}))

function asset(overrides: Partial<Asset>): Asset {
  return {
    id: 'a', user_id: 'u', name: 'Holding', type: 'stock', currency: 'USD',
    units: 1, valuation_method: 'manual', purchase_date: null, purchase_price: null,
    sell_date: null, sell_price: null, growth_type: null, growth_rate: null,
    growth_frequency: null, growth_start_date: null, is_archived: false, position: 0,
    current_value: 100, current_value_primary: 100, gain_loss: null, gain_loss_primary: null,
    value_count: 1, source: 'manual', connection_id: null, isin: null, maturity_date: null,
    group_id: null, ticker: null, ticker_exchange: null, last_price: null, last_price_at: null,
    logo_url: null, average_price: null, total_invested: null, realized_gain: null,
    transaction_count: 0,
    ...overrides,
  }
}

const active = asset({ id: 'active', name: 'Index Fund' })
const archived = asset({ id: 'archived', name: 'Synced 401k Holding', is_archived: true })

describe('AssetsPage archived holdings (#1046)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.assets.list.mockImplementation(async (includeArchived: boolean) =>
      includeArchived ? [active, archived] : [active],
    )
    api.assets.portfolioTrend.mockResolvedValue(null)
  })

  it('hides archived holdings until "Show archived" is ticked', async () => {
    renderWithProviders(<AssetsPage />)

    expect(await screen.findByText('Index Fund')).toBeInTheDocument()
    expect(screen.queryByText('Synced 401k Holding')).not.toBeInTheDocument()
    expect(api.assets.list).toHaveBeenCalledWith(false)

    fireEvent.click(screen.getByRole('checkbox', { name: t('assets.showArchived') }))

    expect(await screen.findByText('Synced 401k Holding')).toBeInTheDocument()
    expect(api.assets.list).toHaveBeenCalledWith(true)
    expect(screen.getByText(t('assets.archivedAssets'))).toBeInTheDocument()
    expect(screen.getByText(t('assets.archivedHint'))).toBeInTheDocument()
    // Only the active holding is part of the portfolio share.
    expect(screen.getAllByText('100.0%')).toHaveLength(1)
  })

  it('remembers the choice', async () => {
    localStorage.setItem('securo.assets.showArchived', 'true')
    renderWithProviders(<AssetsPage />)

    expect(await screen.findByText('Synced 401k Holding')).toBeInTheDocument()
    await waitFor(() => expect(api.assets.list).toHaveBeenCalledWith(true))
    expect(screen.getByRole('checkbox', { name: t('assets.showArchived') })).toBeChecked()
  })

  it('offers the toggle on the Holdings tab only', async () => {
    renderWithProviders(<AssetsPage />)
    expect(await screen.findByRole('checkbox', { name: t('assets.showArchived') })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: t('assets.tabTransactions') }))

    expect(screen.queryByRole('checkbox', { name: t('assets.showArchived') })).not.toBeInTheDocument()
  })
})
