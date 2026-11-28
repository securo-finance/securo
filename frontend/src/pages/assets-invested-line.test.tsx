import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import AssetsPage from '@/pages/assets'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  assets: { list: vi.fn(), archivedCount: vi.fn(), portfolioTrend: vi.fn(), portfolioInvested: vi.fn() },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
  fxRates: { status: vi.fn() },
}))

vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
const ui = vi.hoisted(() => ({ privacy: false, wallets: null as string[] | null }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({
    mask: (value: string) => (ui.privacy ? '••••' : value),
    privacyMode: ui.privacy,
    MASK: '••••',
  }),
}))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { is_superuser: false, preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: false }),
}))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeWalletIds: ui.wallets }),
}))
vi.mock('@/lib/page-chat-context', () => ({
  useRegisterPageChatContext: () => {},
}))

const TREND = {
  assets: [{ id: 'asset-1', name: 'Acme Corp', type: 'stock', group_id: null }],
  trend: [
    { date: '2026-01-01', 'asset-1': 1000, _total: 1000 },
    { date: '2026-01-02', 'asset-1': 1200, _total: 1200 },
  ],
  total: 1200,
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  ui.privacy = false
  ui.wallets = null
  api.assets.list.mockResolvedValue([])
  api.assets.archivedCount.mockResolvedValue({ count: 0, by_wallet: {}, ungrouped: 0 })
  api.assets.portfolioTrend.mockResolvedValue(TREND)
  api.assets.portfolioInvested.mockResolvedValue([{ date: '2026-01-01', amount: 1000 }])
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.fxRates.status.mockResolvedValue({ configured: false })
})

// The drawn step line: a path with a trace, not just an empty Recharts layer.
function investedPath(container: HTMLElement) {
  return container.querySelector('.recharts-line path.recharts-curve')?.getAttribute('d') ?? ''
}

describe('Invested line on the portfolio chart', () => {
  it('opens on Total, without the stacked/lines switch', async () => {
    const { container } = renderWithProviders(<AssetsPage />)

    const group = await screen.findByRole('group', { name: t('assets.chartGroupMode') })
    expect(within(group).getByRole('button', { name: t('assets.total') })).toHaveAttribute('aria-pressed', 'true')
    expect(within(group).getByRole('button', { name: t('assets.chartByWallet') })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('group', { name: t('assets.chartDrawMode') })).not.toBeInTheDocument()
    expect(screen.getByTitle(t('assets.chartInvestedHint'))).toBeVisible()
    await waitFor(() => expect(investedPath(container)).not.toBe(''))
  })

  it('shows Net invested and Gain/Loss as total minus invested', async () => {
    renderWithProviders(<AssetsPage />)

    const invested = await screen.findByText(t('assets.chartInvested'), { selector: 'p' })
    expect(invested).toHaveTextContent('$1,000.00')
    const gain = screen.getByText(t('assets.gainLoss'), { selector: 'p' })
    expect(gain).toHaveTextContent('+$200.00')
  })

  it('rounds floating-point drift on collection totals to +$0.00 instead of -$0.00', async () => {
    ui.wallets = ['w1']
    api.assets.portfolioTrend.mockResolvedValue({
      assets: [
        { id: 'a1', name: 'A1', type: 'stock', group_id: 'w1' },
        { id: 'a2', name: 'A2', type: 'stock', group_id: 'w1' },
      ],
      trend: [{ date: '2026-01-01', a1: 100.1, a2: 200.2, _total: 300.3 }],
      total: 300.3,
    })
    api.assets.portfolioInvested.mockResolvedValue([{ date: '2026-01-01', amount: 300.3 }])
    renderWithProviders(<AssetsPage />)

    const gain = await screen.findByText(t('assets.gainLoss'), { selector: 'p' })
    expect(gain).toHaveTextContent('+$0.00')
    expect(gain).not.toHaveTextContent('-$0.00')
  })

  it('drops the Net invested legend entry in wallet view with Lines', async () => {
    const { user, container } = renderWithProviders(<AssetsPage />)

    await screen.findByTitle(t('assets.chartInvestedHint'))
    await user.click(screen.getByRole('button', { name: t('assets.chartByWallet') }))
    expect(screen.getByTitle(t('assets.chartInvestedHint'))).toBeVisible()
    await waitFor(() => expect(investedPath(container)).not.toBe(''))

    await user.click(screen.getByRole('button', { name: t('assets.chartLines') }))
    expect(screen.queryByTitle(t('assets.chartInvestedHint'))).not.toBeInTheDocument()
    await waitFor(() => expect(container.querySelector('.recharts-line')).not.toBeInTheDocument())
  })

  it('shows neither Net invested nor Gain/Loss without points', async () => {
    api.assets.portfolioInvested.mockResolvedValue([])
    renderWithProviders(<AssetsPage />)

    await screen.findByRole('group', { name: t('assets.chartGroupMode') })
    await waitFor(() => expect(api.assets.portfolioInvested).toHaveBeenCalled())
    expect(screen.queryByText(t('assets.chartInvested'))).not.toBeInTheDocument()
    expect(screen.queryByText(t('assets.gainLoss'))).not.toBeInTheDocument()
  })

  it('shows only the active collection wallets in Net invested and Gain/Loss', async () => {
    ui.wallets = ['w2', 'w1']
    api.assets.portfolioTrend.mockResolvedValue({
      assets: [
        { id: 'in-col', name: 'In Collection', type: 'stock', group_id: 'w1' },
        { id: 'out-col', name: 'Outside', type: 'stock', group_id: 'w3' },
      ],
      trend: [{ date: '2026-01-01', 'in-col': 650, 'out-col': 550, _total: 1200 }],
      total: 1200,
    })
    api.assets.portfolioInvested.mockResolvedValue([{ date: '2026-01-01', amount: 500 }])
    renderWithProviders(<AssetsPage />)

    await waitFor(() => expect(api.assets.portfolioInvested).toHaveBeenCalledWith(['w2', 'w1']))
    const invested = await screen.findByText(t('assets.chartInvested'), { selector: 'p' })
    expect(invested).toHaveTextContent('$500.00')
    const gain = screen.getByText(t('assets.gainLoss'), { selector: 'p' })
    expect(gain).toHaveTextContent('+$150.00')
  })

  it('does not ask for invested points when the collection has no wallets', async () => {
    ui.wallets = []
    renderWithProviders(<AssetsPage />)

    await screen.findByRole('group', { name: t('assets.chartGroupMode') })
    expect(api.assets.portfolioInvested).not.toHaveBeenCalled()
  })

  it('shows no figure in the header with privacy mode on', async () => {
    ui.privacy = true
    renderWithProviders(<AssetsPage />)

    const invested = await screen.findByText(t('assets.chartInvested'), { selector: 'p' })
    const gain = screen.getByText(t('assets.gainLoss'), { selector: 'p' })
    // The header block: the total, Net invested and Gain/Loss.
    const header = invested.parentElement?.parentElement as HTMLElement
    for (const text of [header.textContent ?? '', invested.textContent ?? '', gain.textContent ?? '']) {
      expect(text).not.toMatch(/1,?200|1,?000|\$|200\.00/)
    }
    expect(invested).toHaveTextContent('••••')
    expect(gain).toHaveTextContent('••••')
  })
})
