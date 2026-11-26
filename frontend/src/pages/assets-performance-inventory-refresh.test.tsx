import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { QueryClient } from '@tanstack/react-query'

import AssetsPage from '@/pages/assets'
import { renderWithProviders } from '@/test/utils'
import type { Asset, AssetGroup } from '@/types'

const api = vi.hoisted(() => ({
  assets: { list: vi.fn(), portfolioTrend: vi.fn() },
  assetGroups: { list: vi.fn(), create: vi.fn() },
  currencies: { list: vi.fn() },
  fxRates: { status: vi.fn() },
}))

vi.mock('@/lib/api', () => api)
vi.mock('@/components/portfolio-performance', () => ({
  PortfolioPerformance: (props: {
    inventory: { walletIds: string[]; assetIds: string[] } | null
    wallets: AssetGroup[]
  }) => (
    <div>
      <p data-testid="inventory-wallets">{JSON.stringify(props.inventory?.walletIds ?? null)}</p>
      <p data-testid="picker-wallets">{JSON.stringify(props.wallets.map((w) => w.id))}</p>
    </div>
  ),
}))
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
  useWorkspace: () => ({ canWrite: true, canManage: true, current: { id: 'workspace-a' } }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeWalletIds: null }),
}))
vi.mock('@/lib/page-chat-context', () => ({
  useRegisterPageChatContext: () => {},
}))

const wallet1 = { id: 'wallet-1', name: 'Brokerage', position: 0, color: '#000' } as AssetGroup
const wallet2 = { id: 'wallet-2', name: 'Crypto', position: 1, color: '#111' } as AssetGroup

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  localStorage.setItem('securo.assetsTab', 'performance')
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assets.list.mockResolvedValue([] as Asset[])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('inventory after a wallet is created on another tab of the page', () => {
  it('knows the new wallet when the Performance tab is opened again', async () => {
    let served = [wallet1]
    api.assetGroups.list.mockImplementation(() => Promise.resolve(served))
    api.assetGroups.create.mockImplementation(() => {
      served = [wallet1, wallet2]
      return Promise.resolve(wallet2)
    })
    // The app's own defaults (App.tsx): five minutes of staleTime.
    const queryClient = new QueryClient({
      defaultOptions: { queries: { staleTime: 1000 * 60 * 5, retry: 1 } },
    })
    const { user } = renderWithProviders(<AssetsPage />, { queryClient })

    await waitFor(() =>
      expect(screen.getByTestId('inventory-wallets')).toHaveTextContent('["wallet-1"]'),
    )

    await user.click(screen.getByRole('button', { name: 'Holdings' }))
    await user.click(screen.getByRole('button', { name: 'New Wallet' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), 'Crypto')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.assetGroups.create).toHaveBeenCalledTimes(1))
    // The page's own wallet list is refetched and shows the new wallet.
    await screen.findByText('Crypto')

    await user.click(screen.getByRole('button', { name: 'Performance' }))
    await waitFor(() =>
      expect(screen.getByTestId('picker-wallets')).toHaveTextContent('["wallet-1","wallet-2"]'),
    )
    await waitFor(
      () =>
        expect(screen.getByTestId('inventory-wallets')).toHaveTextContent(
          '["wallet-1","wallet-2"]',
        ),
    )
  })
})
