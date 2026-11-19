import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  assets: {
    list: vi.fn(),
    portfolioTrend: vi.fn(),
    marketSearch: vi.fn(),
    marketQuote: vi.fn(),
  },
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
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeWalletIds: null }),
}))
vi.mock('@/lib/page-chat-context', () => ({
  useRegisterPageChatContext: () => {},
}))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  api.assets.list.mockResolvedValue([])
  api.assets.portfolioTrend.mockResolvedValue({ assets: [], trend: [], total: 0 })
  api.assets.marketSearch.mockResolvedValue([])
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

describe('Ticker search on the Assets page', () => {
  it('asks the API to prefer the pair in the user currency (issue #621)', async () => {
    const { user } = renderWithProviders(<AssetsPage />)

    await user.click(await screen.findByRole('button', { name: t('assets.addAsset') }))
    await user.click(await screen.findByRole('button', { name: t('assets.marketPrice') }))
    await user.type(screen.getByPlaceholderText(t('assets.tickerPlaceholder')), 'cronos')

    // The display currency travels with the debounced search so the backend
    // can rank the coin's pair in BRL first.
    await waitFor(() =>
      expect(api.assets.marketSearch).toHaveBeenCalledWith('cronos', 10, 'BRL'),
    )
  })
})
