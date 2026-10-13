import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import DashboardPage from '@/pages/dashboard'
import { TooltipProvider } from '@/components/ui/tooltip'
import { formatCurrency } from '@/lib/format'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, DashboardSummary } from '@/types'

const api = vi.hoisted(() => ({
  dashboard: {
    summary: vi.fn(),
    spendingByCategory: vi.fn(),
    balanceHistory: vi.fn(),
    projectedTransactions: vi.fn(),
  },
  transactions: { list: vi.fn(), calendar: vi.fn() },
  budgets: { comparison: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: { list: vi.fn() },
  goals: { summary: vi.fn() },
  groups: { list: vi.fn() },
  payees: { list: vi.fn() },
  rules: { create: vi.fn() },
  auth: { updateMe: vi.fn() },
}))

const authState = vi.hoisted(() => ({
  user: { preferences: { currency_display: 'USD' } as Record<string, unknown> },
  updateUser: vi.fn(),
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => authState,
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

function account(overrides: Partial<Account>): Account {
  return {
    id: 'acc',
    user_id: 'user-1',
    connection_id: null,
    external_id: null,
    name: 'Account',
    display_name: null,
    masked_number: null,
    institution_name: null,
    institution_logo_url: null,
    type: 'checking',
    balance: 0,
    current_balance: 0,
    previous_balance: null,
    balance_primary: null,
    currency: 'USD',
    credit_limit: null,
    available_credit: null,
    statement_close_day: null,
    payment_due_day: null,
    next_close_date: null,
    next_due_date: null,
    minimum_payment: null,
    card_brand: null,
    card_level: null,
    shared_balance_group: null,
    is_closed: false,
    closed_at: null,
    ...overrides,
  }
}

// Two cards on one shared credit line both report the line's full -300.
const accounts = [
  account({ id: 'checking', name: 'Checking', current_balance: 1000 }),
  account({ id: 'card-a', name: 'Card A', type: 'credit_card', current_balance: -300, shared_balance_group: 'line-1' }),
  account({ id: 'card-b', name: 'Card B', type: 'credit_card', current_balance: -300, shared_balance_group: 'line-1' }),
  account({ id: 'card-c', name: 'Card C', type: 'credit_card', current_balance: -200 }),
]

const summary: DashboardSummary = {
  total_balance: { USD: 500 },
  total_balance_primary: 500,
  projected_balance: { USD: 500 },
  projected_balance_primary: 500,
  balance_date: '2026-09-12',
  monthly_income: 0,
  monthly_expenses: 0,
  monthly_income_primary: 0,
  monthly_expenses_primary: 0,
  accounts_count: accounts.length,
  pending_categorization: 0,
  pending_categorization_amount: 0,
  assets_value: {},
  assets_value_primary: 0,
  primary_currency: 'USD',
  pending_shares_net: 0,
}

beforeEach(() => {
  vi.clearAllMocks()
  authState.user = { preferences: { currency_display: 'USD' } }
  api.dashboard.summary.mockResolvedValue(summary)
  api.dashboard.spendingByCategory.mockResolvedValue([])
  api.dashboard.balanceHistory.mockResolvedValue({ current: [], previous: [] })
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
  api.budgets.comparison.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue(accounts)
  api.goals.summary.mockResolvedValue([])
  api.groups.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
})

describe('Dashboard net worth breakdown', () => {
  it('counts a shared credit line once in the credit card total', async () => {
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    await user.hover(screen.getByRole('button', { name: 'i' }))

    const tooltip = await screen.findByRole('tooltip')
    const cardRow = within(tooltip).getByText(t('dashboard.creditCardBalance')).parentElement!
    expect(cardRow).toHaveTextContent(formatCurrency(-500, 'USD', 'en-US'))
  })
})

describe('Dashboard layout', () => {
  const goal = {
    id: 'g1',
    name: 'Emergency fund',
    current_amount: 100,
    target_amount: 1000,
    currency: 'USD',
    percentage: 10,
    color: null,
    icon: null,
    monthly_contribution: null,
    on_track: null,
  }

  it('does not render or fetch a hidden widget', async () => {
    api.goals.summary.mockResolvedValue([goal])
    authState.user = {
      preferences: { currency_display: 'USD', dashboard_layout: { order: [], hidden: ['goals'] } },
    }

    renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await screen.findByText(t('dashboard.periodTransactions'))
    expect(screen.queryByText(t('goals.dashboardTitle'))).not.toBeInTheDocument()
    expect(api.goals.summary).not.toHaveBeenCalled()
  })

  it('renders widgets in the saved order', async () => {
    authState.user = {
      preferences: { currency_display: 'USD', dashboard_layout: { order: ['transactions'], hidden: [] } },
    }

    renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    const transactions = await screen.findByText(t('dashboard.periodTransactions'))
    const balance = screen.getByText(t('dashboard.availableBalance'))
    expect(transactions.compareDocumentPosition(balance) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('saves the layout into the user preferences', async () => {
    const updated = { preferences: { currency_display: 'USD', dashboard_layout: { order: [], hidden: ['goals'] } } }
    api.auth.updateMe.mockResolvedValue(updated)

    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await user.click(await screen.findByRole('button', { name: t('dashboard.customize.title') }))
    await user.click(screen.getByRole('switch', { name: t('dashboard.customize.widgets.goals') }))
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(api.auth.updateMe).toHaveBeenCalledWith({
      preferences: {
        currency_display: 'USD',
        dashboard_layout: expect.objectContaining({ hidden: ['goals'] }),
      },
    })
    expect(authState.updateUser).toHaveBeenCalledWith(updated)
  })
})