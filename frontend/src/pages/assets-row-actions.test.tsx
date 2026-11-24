import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset } from '@/types'

const api = vi.hoisted(() => ({
  assets: {
    list: vi.fn(), portfolioTrend: vi.fn(), refreshPrice: vi.fn(), delete: vi.fn(), update: vi.fn(),
    values: vi.fn(), valueTrend: vi.fn(), transactions: vi.fn(),
  },
  assetGroups: { list: vi.fn() },
  accounts: { list: vi.fn() },
  currencies: { list: vi.fn() },
  fxRates: { status: vi.fn() },
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), info: vi.fn(), error: vi.fn() }))
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
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))
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

function asset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'asset-1', user_id: 'user-1', name: 'Apple Inc.', type: 'stock', currency: 'USD',
    units: 2, valuation_method: 'market_price', purchase_date: null, purchase_price: null,
    sell_date: null, sell_price: null, growth_type: null, growth_rate: null,
    growth_frequency: null, growth_start_date: null, is_archived: false, position: 0,
    current_value: 400, current_value_primary: 400, gain_loss: null, gain_loss_primary: null,
    value_count: 0, source: 'manual', connection_id: null, isin: null, maturity_date: null,
    group_id: null, ticker: 'AAPL', ticker_exchange: null, last_price: 200, last_price_at: null,
    logo_url: null, provider_value_date: null, average_price: 150, total_invested: 300,
    realized_gain: null, transaction_count: 1,
    ...overrides,
  }
}

const MANUAL = asset({ id: 'asset-manual', name: 'Old Car', ticker: null, valuation_method: 'manual', type: 'vehicle' })

function openMenu(label: string) {
  fireEvent.pointerDown(screen.getByRole('button', { name: `${t('common.more')}: ${label}` }), {
    button: 0, ctrlKey: false, pointerType: 'mouse',
  })
}

const menuNames = () => screen.getAllByRole('menuitem').map((i) => i.textContent)

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assets.values.mockResolvedValue([])
  api.assets.valueTrend.mockResolvedValue([])
  api.assets.transactions.mockResolvedValue([])
  api.assets.refreshPrice.mockResolvedValue(asset({ last_price: 210 }))
  api.assetGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Holding row actions menu', () => {
  it('replaces the loose icons with one "more" button', async () => {
    api.assets.list.mockResolvedValue([asset()])
    renderWithProviders(<AssetsPage />)

    await screen.findByText('Apple Inc.')
    expect(screen.getByRole('button', { name: `${t('common.more')}: AAPL` })).toBeVisible()
    expect(screen.queryByRole('button', { name: t('common.delete') })).not.toBeInTheDocument()
  })

  it('lists every action for a market-priced holding', async () => {
    api.assets.list.mockResolvedValue([asset()])
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Apple Inc.')

    openMenu('AAPL')

    await screen.findByRole('menu')
    expect(menuNames()).toEqual([
      t('common.edit'), t('assets.addTransaction'), t('assets.refreshPrice'),
      t('assets.moveToWallet'), t('common.delete'),
    ])
  })

  it('leaves out the quote actions for a manual holding', async () => {
    api.assets.list.mockResolvedValue([MANUAL])
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Old Car')

    openMenu('Old Car')

    await screen.findByRole('menu')
    expect(menuNames()).toEqual([t('common.edit'), t('assets.moveToWallet'), t('common.delete')])
  })

  it('does not expand the row when the menu opens', async () => {
    api.assets.list.mockResolvedValue([MANUAL])
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Old Car')

    // The whole click, not only the pointer-down that opens the menu: it is
    // the click that would reach the row and expand it.
    const trigger = screen.getByRole('button', { name: `${t('common.more')}: Old Car` })
    openMenu('Old Car')
    fireEvent.click(trigger)
    const menu = await screen.findByRole('menu')
    fireEvent.click(within(menu).getByRole('menuitem', { name: t('assets.moveToWallet') }))

    await screen.findByRole('dialog')
    expect(api.assets.values).not.toHaveBeenCalled()
  })

  it('opens the same menu on right-click', async () => {
    api.assets.list.mockResolvedValue([asset()])
    renderWithProviders(<AssetsPage />)
    const row = (await screen.findByText('Apple Inc.')).closest('.grid') as HTMLElement

    fireEvent.contextMenu(row)

    await screen.findByRole('menu')
    expect(menuNames()).toContain(t('assets.refreshPrice'))
  })

  it('disables Edit for a holding a provider owns', async () => {
    api.assets.list.mockResolvedValue([asset({ ...MANUAL, source: 'pluggy', connection_id: 'conn-1' })])
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Old Car')

    openMenu('Old Car')

    const edit = await screen.findByRole('menuitem', { name: new RegExp(`^${t('common.edit')}`) })
    expect(edit).toHaveAttribute('data-disabled')
    // Shown as text: a disabled item gets no hover for a tooltip.
    expect(within(edit).getByText(t('assets.syncedReadOnly'))).toBeVisible()
  })

  it('offers no price refresh for a sold holding', async () => {
    api.assets.list.mockResolvedValue([asset({ sell_date: '2026-01-10', sell_price: 210 })])
    // Sold holdings sit in a collapsed section until it is opened.
    localStorage.setItem('securo:assets:showSold', '1')
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Apple Inc.')

    openMenu('AAPL')

    await screen.findByRole('menu')
    expect(menuNames()).toContain(t('assets.addTransaction'))
    expect(menuNames()).not.toContain(t('assets.refreshPrice'))
  })

  it('refreshes the price from the row', async () => {
    api.assets.list.mockResolvedValue([asset()])
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Apple Inc.')
    expect(screen.queryByText('$210.00')).not.toBeInTheDocument()
    api.assets.list.mockResolvedValue([asset({ last_price: 210 })])

    openMenu('AAPL')
    fireEvent.click(await screen.findByRole('menuitem', { name: t('assets.refreshPrice') }))

    await waitFor(() => expect(api.assets.refreshPrice).toHaveBeenCalledWith('asset-1'))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(t('assets.priceRefreshed')))
    // The row shows the new quote once the list is fetched again.
    expect(await screen.findByText('$210.00')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps a refresh from the row out of another holding\'s edit dialog', async () => {
    api.assets.list.mockResolvedValue([asset(), MANUAL])
    api.assets.update.mockResolvedValue(MANUAL)
    let answer: (value: Asset) => void = () => {}
    api.assets.refreshPrice.mockReturnValue(new Promise<Asset>((resolve) => { answer = resolve }))
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Old Car')

    // The quote arrives while the user is already editing another holding.
    openMenu('AAPL')
    fireEvent.click(await screen.findByRole('menuitem', { name: t('assets.refreshPrice') }))
    openMenu('Old Car')
    fireEvent.click(await screen.findByRole('menuitem', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')
    answer(asset({ last_price: 210 }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(t('assets.priceRefreshed')))

    fireEvent.click(within(dialog).getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.assets.update).toHaveBeenCalled())
    expect(api.assets.update.mock.calls[0][0]).toBe('asset-manual')
  })

  it('asks for confirmation before deleting', async () => {
    api.assets.list.mockResolvedValue([MANUAL])
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Old Car')

    openMenu('Old Car')
    fireEvent.click(await screen.findByRole('menuitem', { name: t('common.delete') }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(t('assets.confirmDelete'))).toBeVisible()
    expect(api.assets.delete).not.toHaveBeenCalled()
  })

  it('opens the wallet picker from Move to wallet', async () => {
    api.assets.list.mockResolvedValue([MANUAL])
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Old Car')

    openMenu('Old Car')
    fireEvent.click(await screen.findByRole('menuitem', { name: t('assets.moveToWallet') }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(t('assets.moveToWallet'))).toBeVisible()
  })

  it('shows no menu to a viewer', async () => {
    workspace.canWrite = false
    api.assets.list.mockResolvedValue([asset()])
    renderWithProviders(<AssetsPage />)
    const row = (await screen.findByText('Apple Inc.')).closest('.grid') as HTMLElement

    expect(screen.queryByRole('button', { name: `${t('common.more')}: AAPL` })).not.toBeInTheDocument()
    fireEvent.contextMenu(row)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
