import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

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

function sold(id: string, name: string, over: Partial<Asset> = {}): Asset {
  return {
    id, user_id: 'user-1', name, type: 'stock', currency: 'USD',
    units: null, valuation_method: 'market_price', purchase_date: '2024-01-10', purchase_price: null,
    sell_date: '2025-03-20', sell_price: null, growth_type: null, growth_rate: null,
    growth_frequency: null, growth_start_date: null, is_archived: false, position: 0,
    current_value: null, current_value_primary: null, gain_loss: null, gain_loss_primary: null,
    value_count: 0, source: 'manual', connection_id: null, isin: null, maturity_date: null,
    group_id: null, ticker: null, ticker_exchange: null, last_price: null, last_price_at: null,
    logo_url: null, provider_value_date: null, average_price: null, total_invested: null,
    realized_gain: 250, transaction_count: 2,
    average_buy_price: 10, average_sell_price: 12.5, sold_cost: 1000, sale_proceeds: 1250,
    ...over,
  }
}

const ACME = sold('asset-acme', 'Acme Corp')
const EURO = sold('asset-euro', 'Euro Fund', { currency: 'EUR', realized_gain: -40, sold_cost: 400, sale_proceeds: 360 })

beforeEach(() => {
  vi.clearAllMocks()
  vi.restoreAllMocks()
  localStorage.clear()
  api.assets.list.mockImplementation((includeArchived: boolean) =>
    Promise.resolve([ACME, EURO].filter((a) => includeArchived || !a.is_archived)),
  )
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Sold holdings on the Assets page', () => {
  it('starts collapsed with the count and the realized total per currency', async () => {
    renderWithProviders(<AssetsPage />)

    const header = await screen.findByRole('button', { name: new RegExp(t('assets.soldAssets')) })
    expect(header).toHaveAttribute('aria-expanded', 'false')
    expect(within(header).getByText(new RegExp(`2 ${t('assets.itemsCount')}`))).toBeVisible()
    expect(within(header).getByText(/\+\$250\.00/)).toBeVisible()
    expect(screen.queryByText(t('assets.colAvgSellPrice'))).not.toBeInTheDocument()
    expect(screen.queryByText('Acme Corp')).not.toBeInTheDocument()
  })

  it('opens on click, shows the closed-position columns and remembers it', async () => {
    const { user, unmount } = renderWithProviders(<AssetsPage />)

    await user.click(await screen.findByRole('button', { name: new RegExp(t('assets.soldAssets')) }))

    expect(await screen.findByText(t('assets.colAvgSellPrice'))).toBeVisible()
    for (const key of ['colDates', 'colAvgBuyPrice', 'colInvested', 'colProceeds', 'colResult']) {
      expect(screen.getByText(t(`assets.${key}`))).toBeVisible()
    }
    const row = screen.getByText('Acme Corp').closest('.grid') as HTMLElement
    for (const text of ['$10.00', '$12.50', '$1,000.00', '$1,250.00', '+$250.00', '+25.0%']) {
      expect(within(row).getByText(text)).toBeVisible()
    }
    expect(localStorage.getItem('securo:assets:showSold')).toBe('1')

    unmount()
    renderWithProviders(<AssetsPage />)
    expect(await screen.findByText(t('assets.colAvgSellPrice'))).toBeVisible()
  })

  it('adds up the result per currency, primary currency first', async () => {
    renderWithProviders(<AssetsPage />)

    const header = await screen.findByRole('button', { name: new RegExp(t('assets.soldAssets')) })
    const figures = within(header).getAllByText(/\d/).filter((el) => /^[+-]/.test(el.textContent ?? ''))
    expect(figures.map((el) => el.textContent)).toEqual(['+$250.00', expect.stringMatching(/^-.*40\.00/)])
  })

  it('drops the Sold badge in the section but keeps it for archived holdings', async () => {
    localStorage.setItem('securo:assets:showSold', '1')
    localStorage.setItem('securo:assets:showArchived', '1')
    const archivedSold = sold('asset-old', 'Old Corp', { is_archived: true })
    api.assets.list.mockImplementation((includeArchived: boolean) =>
      Promise.resolve([ACME, archivedSold].filter((a) => includeArchived || !a.is_archived)),
    )
    renderWithProviders(<AssetsPage />)

    const acmeRow = (await screen.findByText('Acme Corp')).closest('.grid') as HTMLElement
    expect(within(acmeRow).queryByText(t('assets.sold'))).not.toBeInTheDocument()
    const oldRow = (await screen.findByText('Old Corp')).closest('.grid') as HTMLElement
    expect(within(oldRow).getByText(t('assets.sold'))).toBeVisible()
  })

  it('renders collapsed when localStorage throws', async () => {
    const getItem = Storage.prototype.getItem
    const setItem = Storage.prototype.setItem
    const key = 'securo:assets:showSold'
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, k: string) {
      if (k === key) throw new Error('blocked')
      return getItem.call(this, k)
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, k: string, v: string) {
      if (k === key) throw new Error('blocked')
      setItem.call(this, k, v)
    })
    const { user } = renderWithProviders(<AssetsPage />)

    const header = await screen.findByRole('button', { name: new RegExp(t('assets.soldAssets')) })
    expect(header).toHaveAttribute('aria-expanded', 'false')
    await user.click(header)
    expect(header).toHaveAttribute('aria-expanded', 'true')
  })
})
