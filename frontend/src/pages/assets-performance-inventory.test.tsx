import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
import { renderWithProviders } from '@/test/utils'
import type { Asset, AssetGroup } from '@/types'

const api = vi.hoisted(() => ({
  assets: { list: vi.fn(), portfolioTrend: vi.fn() },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
  fxRates: { status: vi.fn() },
}))

vi.mock('@/lib/api', () => api)
// The tab itself is tested on its own; here only what the page hands it.
vi.mock('@/components/portfolio-performance', () => ({
  PortfolioPerformance: (props: {
    inventory: { walletIds: string[]; assetIds: string[] } | null
    inventoryError?: boolean
    onRetryInventory?: () => void
  }) => (
    <div>
      <p data-testid="inventory">{JSON.stringify(props.inventory)}</p>
      {props.inventoryError && (
        <button type="button" onClick={props.onRetryInventory}>
          retry inventory
        </button>
      )}
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
  useWorkspace: () => ({ canWrite: false, canManage: false, current: { id: 'workspace-a' } }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeWalletIds: null }),
}))
vi.mock('@/lib/page-chat-context', () => ({
  useRegisterPageChatContext: () => {},
}))

const holdings = [{ id: 'asset-1' }, { id: 'asset-archived' }] as Asset[]
const wallets = [{ id: 'wallet-1', name: 'Brokerage', position: 0 }] as AssetGroup[]

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  localStorage.setItem('securo.assetsTab', 'performance')
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assets.list.mockResolvedValue(holdings)
  api.assetGroups.list.mockResolvedValue(wallets)
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Performance inventory on the Assets page', () => {
  it('reads the inventory from the workspace on screen, not the shared default', async () => {
    renderWithProviders(<AssetsPage />)

    await waitFor(() =>
      expect(screen.getByTestId('inventory')).toHaveTextContent(
        JSON.stringify({ walletIds: ['wallet-1'], assetIds: ['asset-1', 'asset-archived'] }),
      ),
    )
    expect(api.assets.list).toHaveBeenCalledWith(true, 'workspace-a')
    expect(api.assetGroups.list).toHaveBeenCalledWith('workspace-a')
  })

  it('reports a failed inventory read and retries it', async () => {
    api.assets.list.mockImplementation((includeArchived: boolean, workspaceId?: string) =>
      includeArchived && workspaceId
        ? Promise.reject(new Error('offline'))
        : Promise.resolve(holdings),
    )
    const { user } = renderWithProviders(<AssetsPage />)

    const retry = await screen.findByRole('button', { name: 'retry inventory' })
    expect(screen.getByTestId('inventory')).toHaveTextContent('null')

    api.assets.list.mockResolvedValue(holdings)
    await user.click(retry)

    await waitFor(() =>
      expect(screen.getByTestId('inventory')).toHaveTextContent(
        JSON.stringify({ walletIds: ['wallet-1'], assetIds: ['asset-1', 'asset-archived'] }),
      ),
    )
    expect(screen.queryByRole('button', { name: 'retry inventory' })).not.toBeInTheDocument()
  })
})
