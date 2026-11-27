import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'
import type { AssetGroup } from '@/types'

const api = vi.hoisted(() => ({
  assets: { list: vi.fn(), portfolioTrend: vi.fn() },
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

function wallet(name: string, position: number): AssetGroup {
  return {
    id: name.toLowerCase(), user_id: 'user-1', name, icon: 'wallet', color: '#0EA5E9', position,
    source: 'manual', connection_id: null, institution_name: null, asset_count: 0,
    current_value: 0, current_value_primary: 0,
  }
}

const WALLETS = [wallet('Brokerage', 0), wallet('Pension', 1), wallet('Savings', 2)]

function openMenu(name: string) {
  fireEvent.pointerDown(screen.getByRole('button', { name: `${t('common.more')}: ${name}` }), {
    button: 0, ctrlKey: false, pointerType: 'mouse',
  })
}

const handle = (name: string) => screen.queryByRole('button', { name: t('assets.reorderWallet', { name }) })

/** The wallet names in the order the page draws them. */
const shownOrder = () =>
  screen.getAllByRole('button', { name: new RegExp(`^${t('common.more')}: `) })
    .map((button) => button.getAttribute('aria-label')?.split(': ')[1])

async function renderPage() {
  renderWithProviders(<AssetsPage />)
  await screen.findByRole('button', { name: `${t('common.more')}: Brokerage` })
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  api.assets.list.mockResolvedValue([])
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assetGroups.list.mockResolvedValue(WALLETS)
  api.accounts.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Wallet ordering', () => {
  it('moves a wallet down from its menu and saves the whole order', async () => {
    let finish = () => {}
    api.assetGroups.reorder.mockReturnValue(new Promise<void>((resolve) => { finish = resolve }))
    await renderPage()

    openMenu('Brokerage')
    fireEvent.click(await screen.findByRole('menuitem', { name: t('assets.moveWalletDown') }))

    await waitFor(() => expect(api.assetGroups.reorder).toHaveBeenCalledWith(['pension', 'brokerage', 'savings']))
    // Drawn in the new order while the save is still pending.
    await waitFor(() => expect(shownOrder()).toEqual(['Pension', 'Brokerage', 'Savings']))
    finish()
  })

  it('puts the old order back when saving fails', async () => {
    api.assetGroups.reorder.mockRejectedValue(new Error('offline'))
    await renderPage()

    openMenu('Savings')
    fireEvent.click(await screen.findByRole('menuitem', { name: t('assets.moveWalletUp') }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(t('common.error')))
    expect(shownOrder()).toEqual(['Brokerage', 'Pension', 'Savings'])
  })

  it('disables the move that would leave the list', async () => {
    await renderPage()

    openMenu('Brokerage')

    expect(await screen.findByRole('menuitem', { name: t('assets.moveWalletUp') })).toHaveAttribute('data-disabled')
    expect(screen.getByRole('menuitem', { name: t('assets.moveWalletDown') })).not.toHaveAttribute('data-disabled')
  })

  it('offers no ordering when there is only one wallet', async () => {
    api.assetGroups.list.mockResolvedValue([WALLETS[0]])
    await renderPage()

    expect(handle('Brokerage')).not.toBeInTheDocument()
    openMenu('Brokerage')
    await screen.findByRole('menu')
    expect(screen.queryByRole('menuitem', { name: t('assets.moveWalletDown') })).not.toBeInTheDocument()
  })

  it('shows a drag handle per wallet to someone who can write', async () => {
    await renderPage()

    for (const { name } of WALLETS) expect(handle(name)).toBeInTheDocument()
  })

  it('shows a viewer neither handles nor the wallet menu', async () => {
    workspace.canWrite = false
    renderWithProviders(<AssetsPage />)
    await screen.findByText('Brokerage')

    expect(handle('Brokerage')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `${t('common.more')}: Brokerage` })).not.toBeInTheDocument()
  })
})
