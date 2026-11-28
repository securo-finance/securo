import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset } from '@/types'

const api = vi.hoisted(() => ({
  assets: { list: vi.fn(), portfolioTrend: vi.fn(), reorder: vi.fn(), values: vi.fn() },
  assetGroups: { list: vi.fn(), reorder: vi.fn() },
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

function holding(name: string, overrides: Partial<Asset> = {}): Asset {
  return {
    id: name.toLowerCase(), user_id: 'user-1', name, type: 'investment', currency: 'USD',
    units: null, valuation_method: 'manual', purchase_date: null, purchase_price: null,
    sell_date: null, sell_price: null, growth_type: null, growth_rate: null,
    growth_frequency: null, growth_start_date: null, is_archived: false, position: 0,
    current_value: 100, current_value_primary: 100, gain_loss: null, gain_loss_primary: null,
    value_count: 1, source: 'manual', connection_id: null, isin: null, maturity_date: null,
    group_id: null, ticker: null, ticker_exchange: null, last_price: null, last_price_at: null,
    logo_url: null, provider_value_date: null, average_price: null, total_invested: null,
    realized_gain: null, transaction_count: 0,
    ...overrides,
  }
}

const HOLDINGS = [holding('Alpha'), holding('Beta'), holding('Gamma')]

function openMenu(name: string) {
  fireEvent.pointerDown(screen.getByRole('button', { name: `${t('common.more')}: ${name}` }), {
    button: 0, ctrlKey: false, pointerType: 'mouse',
  })
}

const handle = (name: string) => screen.queryByRole('button', { name: t('assets.reorder', { name }) })

/** The holding names in the order the page draws them. */
const shownOrder = () =>
  screen.getAllByRole('button', { name: new RegExp(`^${t('common.more')}: `) })
    .map((button) => button.getAttribute('aria-label')?.split(': ')[1])

async function renderPage() {
  renderWithProviders(<AssetsPage />)
  await screen.findByText('Alpha')
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  api.assets.list.mockResolvedValue(HOLDINGS)
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assets.values.mockResolvedValue([])
  api.assetGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Holding ordering', () => {
  it('moves a holding down from its menu and saves the order', async () => {
    api.assets.reorder.mockReturnValue(new Promise<void>(() => {}))
    await renderPage()

    openMenu('Alpha')
    fireEvent.click(await screen.findByRole('menuitem', { name: t('assets.moveDown') }))

    await waitFor(() => expect(api.assets.reorder).toHaveBeenCalledWith(['beta', 'alpha']))
    // Drawn in the new order while the save is still pending.
    await waitFor(() => expect(shownOrder()).toEqual(['Beta', 'Alpha', 'Gamma']))
  })

  it('puts the old order back when saving fails', async () => {
    api.assets.reorder.mockRejectedValue(new Error('offline'))
    // The refetch after the error never answers, so only the rollback can restore the order.
    api.assets.list.mockResolvedValueOnce(HOLDINGS).mockReturnValue(new Promise(() => {}))
    await renderPage()

    openMenu('Gamma')
    fireEvent.click(await screen.findByRole('menuitem', { name: t('assets.moveUp') }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(t('common.error')))
    expect(shownOrder()).toEqual(['Alpha', 'Beta', 'Gamma'])
  })

  it('disables the move that would leave the list', async () => {
    await renderPage()

    openMenu('Gamma')

    expect(await screen.findByRole('menuitem', { name: t('assets.moveDown') })).toHaveAttribute('data-disabled')
    expect(screen.getByRole('menuitem', { name: t('assets.moveUp') })).not.toHaveAttribute('data-disabled')
  })

  it('does not open the holding on a click of its handle', async () => {
    await renderPage()

    fireEvent.click(handle('Alpha')!)

    expect(api.assets.reorder).not.toHaveBeenCalled()
    expect(screen.queryByText(t('assets.addValue'))).not.toBeInTheDocument()

    // The same click on the row itself does open it.
    fireEvent.click(screen.getByText('Alpha'))
    expect(await screen.findByText(t('assets.addValue'))).toBeInTheDocument()
  })

  it('offers no ordering for a holding alone in its wallet', async () => {
    api.assets.list.mockResolvedValue([HOLDINGS[0]])
    await renderPage()

    expect(handle('Alpha')).not.toBeInTheDocument()
    openMenu('Alpha')
    await screen.findByRole('menu')
    expect(screen.queryByRole('menuitem', { name: t('assets.moveUp') })).not.toBeInTheDocument()
  })

  it('offers no ordering for archived holdings', async () => {
    localStorage.setItem('securo:assets:showArchived', '1')
    api.assets.list.mockImplementation(async (includeArchived: boolean) =>
      includeArchived
        ? [holding('Alpha', { is_archived: true }), holding('Beta', { is_archived: true })]
        : [],
    )
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Alpha')

    expect(handle('Alpha')).not.toBeInTheDocument()
    localStorage.removeItem('securo:assets:showArchived')
  })

  it('shows a viewer no handles', async () => {
    workspace.canWrite = false
    await renderPage()

    expect(handle('Alpha')).not.toBeInTheDocument()
  })
})
