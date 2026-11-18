import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import AccountDetailPage from '@/pages/account-detail'
import { i18n, renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  accounts: { get: vi.fn(), bills: vi.fn(), summary: vi.fn(), list: vi.fn(), update: vi.fn(), setDefault: vi.fn() },
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
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

const account = {
  id: 'acc-1', name: 'Checking', type: 'checking', currency: 'USD',
  balance: 100, current_balance: 100, is_closed: false,
}

beforeEach(async () => {
  vi.clearAllMocks()
  await i18n.changeLanguage('en')
  api.accounts.list.mockResolvedValue([account])
  api.accounts.summary.mockResolvedValue({
    opening_balance: 100, current_balance: 100, monthly_income: 0, monthly_expenses: 0,
    projected_income: 0, projected_expenses: 0,
  })
  api.accounts.setDefault.mockResolvedValue({ account_id: null })
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
})

function renderPage() {
  return renderWithProviders(<AccountDetailPage />, {
    route: `/accounts/${account.id}`, path: '/accounts/:id',
  })
}

describe('account-detail default account (#460)', () => {
  it('makes the account the default for new transactions', async () => {
    api.accounts.get.mockResolvedValue({ ...account, is_default: false })
    const { user } = renderPage()

    const toggle = await screen.findByRole('button', { name: i18n.t('accounts.defaultAccount') })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await user.click(toggle)

    await waitFor(() => expect(api.accounts.setDefault).toHaveBeenCalledWith('acc-1'))
  })

  it('clears the default when it is already this account', async () => {
    api.accounts.get.mockResolvedValue({ ...account, is_default: true })
    const { user } = renderPage()

    const toggle = await screen.findByRole('button', { name: i18n.t('accounts.defaultAccount') })
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    await user.click(toggle)

    await waitFor(() => expect(api.accounts.setDefault).toHaveBeenCalledWith(null))
  })
})
