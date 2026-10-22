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
const auth = vi.hoisted(() => ({ isSuperuser: true }))

vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false }),
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { is_superuser: auth.isSuperuser, preferences: { currency_display: 'USD' } } }),
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

function asset(currency: string): Asset {
  return {
    id: 'asset-1', user_id: 'user-1', name: 'Holding', type: 'other', currency,
    units: null, valuation_method: 'manual', purchase_date: null, purchase_price: null,
    sell_date: null, sell_price: null, growth_type: null, growth_rate: null,
    growth_frequency: null, growth_start_date: null, is_archived: false, position: 0,
    current_value: 100, current_value_primary: null, gain_loss: null, gain_loss_primary: null,
    value_count: 0, source: 'manual', connection_id: null, isin: null, maturity_date: null,
    group_id: null, ticker: null, ticker_exchange: null, last_price: null, last_price_at: null,
    logo_url: null, average_price: null, total_invested: null, realized_gain: null,
    transaction_count: 0,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  auth.isSuperuser = true
  api.assets.list.mockResolvedValue([asset('EUR')])
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Assets exchange rate setup warning', () => {
  it('links an admin to exchange rate settings when a valued asset needs conversion', async () => {
    renderWithProviders(<AssetsPage />)

    const warning = (await screen.findByText(t('assets.exchangeRateSetupWarning'))).closest('[role="status"]')!
    expect(within(warning as HTMLElement).getByRole('link', { name: t('assets.exchangeRateSetupLink') }))
      .toHaveAttribute('href', '/admin#provider-exchangeRates')
    expect(screen.getByRole('button', { name: t('assets.exchangeRateSetupAction', { from: 'EUR', to: 'USD' }) }))
      .toBeVisible()
    expect(api.fxRates.status).toHaveBeenCalledTimes(1)
  })

  it('does not request exchange rate status when all valued assets use the display currency', async () => {
    api.assets.list.mockResolvedValue([asset('USD')])
    renderWithProviders(<AssetsPage />)

    await screen.findByText('Holding')
    expect(screen.queryByText(t('assets.exchangeRateSetupWarning'))).not.toBeInTheDocument()
    expect(api.fxRates.status).not.toHaveBeenCalled()
  })

  it('does not warn or request exchange rate status for a zero-value foreign asset', async () => {
    api.assets.list.mockResolvedValue([{ ...asset('EUR'), current_value: 0 }])
    renderWithProviders(<AssetsPage />)

    await screen.findByText('Holding')
    expect(screen.queryByText(t('assets.exchangeRateSetupWarning'))).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('assets.exchangeRateSetupAction', { from: 'EUR', to: 'USD' }) })).not.toBeInTheDocument()
    expect(api.fxRates.status).not.toHaveBeenCalled()
  })

  it('shows non-admin guidance without an admin link', async () => {
    auth.isSuperuser = false
    renderWithProviders(<AssetsPage />)

    const warning = (await screen.findByText(t('assets.exchangeRateSetupWarning'))).closest('[role="status"]')!
    expect(within(warning as HTMLElement).getByText(t('assets.exchangeRateSetupContactAdmin'))).toBeVisible()
    expect(within(warning as HTMLElement).queryByRole('link')).not.toBeInTheDocument()
    await waitFor(() => expect(api.fxRates.status).toHaveBeenCalledTimes(1))
  })
})
