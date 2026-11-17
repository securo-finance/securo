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

interface ReservedAccountFixture {
  id: string
  name: string
  type: string
  currency: string
  balance: number
  current_balance: number
  is_active: boolean
  reserved_balances?: {
    id: string
    external_id: string
    name: string | null
    currency: string
    amount: number
  }[]
  reserved_total?: number
}

const base = {
  id: 'acc-mp', name: 'Mercado Pago', type: 'checking', currency: 'BRL',
  balance: 1000, current_balance: 1000, is_active: true,
}

// A representative payload: 1,000 in the balance and 2,000 + 2,000 reserved.
const reservedAccount: ReservedAccountFixture = {
  ...base,
  reserved_balances: [
    { id: 'r1', external_id: 'emergency-fund', name: 'Emergency fund', currency: 'BRL', amount: 0 },
    { id: 'r2', external_id: 'round-up', name: 'Round-up savings', currency: 'BRL', amount: 2000 },
    { id: 'r3', external_id: 'marketplace', name: 'Marketplace savings', currency: 'BRL', amount: 2000 },
  ],
  reserved_total: 4000,
}

const summary = {
  opening_balance: 1000, current_balance: 1000, monthly_income: 0, monthly_expenses: 0,
  projected_income: 0, projected_expenses: 0,
}

beforeEach(async () => {
  vi.clearAllMocks()
  await i18n.changeLanguage('en')
  api.accounts.get.mockResolvedValue(reservedAccount)
  api.accounts.list.mockResolvedValue([reservedAccount])
  api.accounts.summary.mockResolvedValue(summary)
  api.accounts.bills.mockResolvedValue([])
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
})

function renderPage(account: ReservedAccountFixture) {
  api.accounts.get.mockResolvedValue(account)
  api.accounts.list.mockResolvedValue([account])
  renderWithProviders(<AccountDetailPage />, {
    route: `/accounts/${account.id}`, path: '/accounts/:id',
  })
}

describe('account-detail reserved balances (ADR 0022)', () => {
  it('lists the cofrinhos including the zeroed one, with the same-currency total', async () => {
    renderPage(reservedAccount)

    expect(await screen.findByText(i18n.t('accounts.reservedBalances'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('accounts.reservedBalancesHint'))).toBeInTheDocument()
    expect(screen.getByText('Emergency fund')).toBeInTheDocument()
    expect(screen.getByText('Round-up savings')).toBeInTheDocument()
    expect(screen.getByText('Marketplace savings')).toBeInTheDocument()
    // The total shares its figure with the balance cards, so read the row.
    const totalRow = screen.getByText(i18n.t('accounts.reservedTotal')).parentElement
    expect(totalRow).toHaveTextContent(/4,000\.00/)
  })

  it('falls back to a localized label when the provider gives no name', async () => {
    renderPage({
      ...base,
      reserved_balances: [
        { id: 'r1', external_id: 'x', name: null, currency: 'BRL', amount: 10 },
      ],
      reserved_total: 10,
    })

    expect(await screen.findByText(i18n.t('accounts.reservedBalanceFallback'))).toBeInTheDocument()
  })

  it('renders nothing when the account has no reserved balances', async () => {
    renderPage({ ...base, reserved_balances: [], reserved_total: 0 })

    await waitFor(() => expect(api.accounts.get).toHaveBeenCalled())
    expect(screen.queryByText(i18n.t('accounts.reservedBalances'))).not.toBeInTheDocument()
  })

  it('renders nothing for a credit card', async () => {
    renderPage({
      ...base,
      id: 'acc-cc',
      type: 'credit_card',
      reserved_balances: reservedAccount.reserved_balances,
      reserved_total: 4000,
    })

    await waitFor(() => expect(api.accounts.get).toHaveBeenCalled())
    expect(screen.queryByText(i18n.t('accounts.reservedBalances'))).not.toBeInTheDocument()
  })
})
