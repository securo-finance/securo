import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor, within } from '@testing-library/react'

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
    await waitFor(() => expect(api.assets.marketSearch).toHaveBeenCalledWith('cronos', 10, 'BRL'))
  })

  it('ignores a response for a query that was already replaced (issue #621)', async () => {
    const { user } = renderWithProviders(<AssetsPage />)

    await user.click(await screen.findByRole('button', { name: t('assets.addAsset') }))
    await user.click(await screen.findByRole('button', { name: t('assets.marketPrice') }))

    let resolveOld: (rows: unknown[]) => void = () => {}
    let resolveNew: (rows: unknown[]) => void = () => {}
    api.assets.marketSearch
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveOld = resolve
        }),
      )
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveNew = resolve
        }),
      )

    const input = screen.getByPlaceholderText(t('assets.tickerPlaceholder'))
    await user.type(input, 'cro')
    await waitFor(() => expect(api.assets.marketSearch).toHaveBeenCalledTimes(1), {
      timeout: 2000,
    })
    await user.type(input, 'nos')
    await waitFor(() => expect(api.assets.marketSearch).toHaveBeenCalledTimes(2), {
      timeout: 2000,
    })

    // The newer query answers first; the older one must not overwrite it when
    // it lands afterwards.
    await act(async () => {
      resolveNew([{ symbol: 'CRO-BRL', name: 'Cronos', exchange: 'CCC', quote_type: 'CRYPTOCURRENCY' }])
    })
    await screen.findByText('CRO-BRL')

    await act(async () => {
      resolveOld([{ symbol: 'STALE-USD', name: 'Stale Cronos', exchange: 'CCC', quote_type: 'CRYPTOCURRENCY' }])
    })
    expect(screen.queryByText('STALE-USD')).not.toBeInTheDocument()
    expect(screen.getByText('CRO-BRL')).toBeInTheDocument()
  })

  it('clears the spinner when the input is emptied mid-search (issue #621)', async () => {
    const { user } = renderWithProviders(<AssetsPage />)

    await user.click(await screen.findByRole('button', { name: t('assets.addAsset') }))
    await user.click(await screen.findByRole('button', { name: t('assets.marketPrice') }))

    let resolveSearch: (rows: unknown[]) => void = () => {}
    api.assets.marketSearch.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSearch = resolve
      }),
    )

    const input = screen.getByPlaceholderText(t('assets.tickerPlaceholder'))
    const tickerBox = input.parentElement as HTMLElement
    await user.type(input, 'cro')

    // The label shows while the debounced request is on the wire.
    await waitFor(() => expect(within(tickerBox).getByText(t('common.loading'))).toBeInTheDocument())
    await waitFor(() => expect(api.assets.marketSearch).toHaveBeenCalledTimes(1), {
      timeout: 2000,
    })

    // Emptying the query abandons the request; the abandoned `finally` never
    // runs, so the effect's own branches have to drop the label.
    await user.clear(input)
    await waitFor(() => expect(within(tickerBox).queryByText(t('common.loading'))).not.toBeInTheDocument())

    // The late response of the abandoned query must not revive it either.
    await act(async () => {
      resolveSearch([{ symbol: 'CRO-BRL', name: 'Cronos', exchange: 'CCC', quote_type: 'CRYPTOCURRENCY' }])
    })
    expect(within(tickerBox).queryByText(t('common.loading'))).not.toBeInTheDocument()
    expect(screen.queryByText('CRO-BRL')).not.toBeInTheDocument()
  })
})
