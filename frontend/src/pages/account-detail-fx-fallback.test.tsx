import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import AccountDetailPage from '@/pages/account-detail'
import { i18n, renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  accounts: { get: vi.fn(), bills: vi.fn(), summary: vi.fn(), list: vi.fn(), update: vi.fn() },
  transactions: { list: vi.fn(), update: vi.fn(), delete: vi.fn(), create: vi.fn() },
  dashboard: { projectedTransactions: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  accounts: api.accounts,
  transactions: api.transactions,
  dashboard: api.dashboard,
  categories: api.categories,
  categoryGroups: api.categoryGroups,
}))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '***' }),
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

const account = {
  id: 'acc-eur', name: 'EUR Wallet', type: 'checking', currency: 'EUR',
  balance: 100, current_balance: 100, is_active: true,
}
const nativeSummary = {
  opening_balance: 100, current_balance: 100, monthly_income: 0, monthly_expenses: 0,
  projected_income: 0, projected_expenses: 0,
}

beforeEach(async () => {
  vi.clearAllMocks()
  await i18n.changeLanguage('en')
  api.accounts.get.mockResolvedValue(account)
  api.accounts.list.mockResolvedValue([account])
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
})

function renderPage() {
  renderWithProviders(<AccountDetailPage />, {
    route: `/accounts/${account.id}`, path: '/accounts/:id',
  })
}

describe('account-detail FX fallback (#1165)', () => {
  it('keeps the primary-currency toggle off when the summary has no real rate', async () => {
    api.accounts.summary.mockResolvedValue({ ...nativeSummary, fx_fallback: true })
    renderPage()

    const primary = await screen.findByRole('button', { name: 'BRL' })
    await waitFor(() => expect(primary).toBeDisabled())
    expect(primary).toHaveAttribute('title', i18n.t('transactions.fxFallbackBanner'))
    expect(screen.getByRole('button', { name: 'EUR' })).toBeEnabled()
  })

  it('allows the primary-currency toggle when the summary is converted', async () => {
    api.accounts.summary.mockResolvedValue({
      ...nativeSummary, current_balance_primary: 600, opening_balance_primary: 600,
      monthly_income_primary: 0, monthly_expenses_primary: 0, fx_fallback: false,
    })
    renderPage()

    const primary = await screen.findByRole('button', { name: 'BRL' })
    await waitFor(() => expect(api.accounts.summary).toHaveBeenCalled())
    expect(primary).toBeEnabled()
    expect(primary).not.toHaveAttribute('title')
  })
})
