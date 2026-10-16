import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useNavigate } from 'react-router-dom'

import DashboardPage from '@/pages/dashboard'
import { TooltipProvider } from '@/components/ui/tooltip'
import { formatCurrency } from '@/lib/format'
import { monthLabel } from '@/lib/month-utils'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, DashboardSummary } from '@/types'

const api = vi.hoisted(() => ({
  dashboard: {
    summary: vi.fn(),
    spendingByCategory: vi.fn(),
    balanceHistory: vi.fn(),
    balanceHistoryMonths: vi.fn(),
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
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
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

function DashboardRouteNavigation() {
  const navigate = useNavigate()
  return (
    <>
      <button onClick={() => navigate('/?month=2026-03&comparison_month=2026-02')}>Navigate to March</button>
      <button onClick={() => navigate('/?month=2026-04&comparison_month=2026-02')}>Navigate to April</button>
    </>
  )
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
  api.dashboard.summary.mockResolvedValue(summary)
  api.dashboard.spendingByCategory.mockResolvedValue([])
  api.dashboard.balanceHistory.mockResolvedValue({
    current: [],
    opening_balance: 0,
    baseline: [],
    comparison: null,
    comparison_month: null,
    comparison_opening_balance: null,
  })
  api.dashboard.balanceHistoryMonths.mockResolvedValue([])
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

describe('Dashboard balance comparisons', () => {
  const month = (value: string) => monthLabel(value, 'en')
  const money = (value: number) => formatCurrency(value, 'USD', 'en-US')
  const signed = (value: number) => `${value >= 0 ? '+' : ''}${money(value)}`

  function renderDashboard(route: string) {
    return renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
      { route },
    )
  }

  const selectorButton = () => screen.findByRole('button', { name: t('dashboard.compareMonths') })

  function mockHistory(overrides: Record<string, unknown>) {
    api.dashboard.balanceHistory.mockResolvedValue({
      current: [{ day: 1, balance: 1100 }],
      opening_balance: 1000,
      baseline: [],
      comparison: null,
      comparison_month: null,
      comparison_opening_balance: null,
      ...overrides,
    })
  }

  it('labels a single comparison month in the selector, legend and footer', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])
    // Current month is +100 since its opening, February was +50: 50 ahead.
    mockHistory({
      comparison: [{ day: 1, balance: 1950 }, { day: 2, balance: 1950 }],
      comparison_month: '2026-02',
      comparison_opening_balance: 1900,
    })

    renderDashboard('/?month=2026-04&comparison_month=2026-02')

    expect(await screen.findByText(
      t('dashboard.balanceFlowVsMonth', { day: 1, month: month('2026-02'), delta: signed(50) }),
    )).toBeInTheDocument()
    expect(await selectorButton()).toHaveTextContent(month('2026-02'))
    // The legend shows the month itself, not the median wording.
    expect(screen.getAllByText(month('2026-02')).some((el) => !el.closest('button'))).toBe(true)
    expect(screen.queryByText(t('dashboard.balanceMedianLegend'))).not.toBeInTheDocument()
    expect(api.dashboard.balanceHistory).toHaveBeenCalledWith('2026-04-01', undefined, ['2026-02-01'])
  })

  it('shows the median and the range with three months, compared against the median', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])
    mockHistory({
      baseline: [{ day: 1, median: 40, lower_quartile: 20, upper_quartile: 60 }],
    })

    renderDashboard('/?month=2026-04&comparison_month=2026-01&comparison_month=2026-02&comparison_month=2026-03')

    expect(await screen.findByText(
      t('dashboard.balanceFlowVsMedian', { day: 1, median: money(40), delta: signed(60) }),
    )).toBeInTheDocument()
    expect(screen.getByText(t('dashboard.balanceMedianLegend'))).toBeInTheDocument()
    expect(screen.getByText(t('dashboard.balanceRangeLegend'))).toBeInTheDocument()
    expect(await selectorButton()).toHaveTextContent(`${t('dashboard.compareMonths')} (3)`)
  })

  it('shows the median but no range when only two months contribute', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])
    mockHistory({
      baseline: [{ day: 1, median: 40, lower_quartile: null, upper_quartile: null }],
    })

    renderDashboard('/?month=2026-04&comparison_month=2026-02&comparison_month=2026-03')

    expect(await screen.findByText(t('dashboard.balanceMedianLegend'))).toBeInTheDocument()
    expect(screen.queryByText(t('dashboard.balanceRangeLegend'))).not.toBeInTheDocument()
  })

  it('defaults to the active months of the previous 12 when the URL has no selection', async () => {
    // 2025-03 is more than 12 months before April 2026, 2026-05 is after it.
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2025-03', '2025-06', '2026-03', '2026-05'])

    renderDashboard('/?month=2026-04')

    // The options load after first paint, so the label settles asynchronously.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: t('dashboard.compareMonths') }))
        .toHaveTextContent(`${t('dashboard.compareMonths')} (2)`)
    })
    await waitFor(() => {
      expect(api.dashboard.balanceHistory).toHaveBeenCalledWith(
        '2026-04-01',
        undefined,
        ['2025-06-01', '2026-03-01'],
      )
    })
  })

  it('follows back and forward navigation between viewed months', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])

    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardRouteNavigation />
        <DashboardPage />
      </TooltipProvider>,
      { route: '/?month=2026-04&comparison_month=2026-02' },
    )

    expect(await screen.findByRole('heading', { name: 'April 2026' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Navigate to March' }))
    expect(await screen.findByRole('heading', { name: 'March 2026' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Navigate to April' }))
    expect(await screen.findByRole('heading', { name: 'April 2026' })).toBeInTheDocument()
  })

  it('offers only earlier months, newest first, and keeps the last selected month checked', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03', '2026-04', '2026-05'])

    const { user } = renderDashboard('/?month=2026-04&comparison_month=2026-02')
    await user.click(await selectorButton())

    const boxes = await screen.findAllByRole('checkbox')
    expect(boxes.map((box) => box.closest('label')?.textContent)).toEqual([
      month('2026-03'),
      month('2026-02'),
      month('2026-01'),
    ])
    expect(screen.getByRole('checkbox', { name: month('2026-03') })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: month('2026-01') })).not.toBeChecked()
    // The only selected month cannot be removed.
    expect(screen.getByRole('checkbox', { name: month('2026-02') })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: month('2026-02') })).toBeDisabled()
  })

  it('adds a month to the comparison when it is checked', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])

    const { user } = renderDashboard('/?month=2026-04&comparison_month=2026-02')
    await user.click(await selectorButton())
    await user.click(await screen.findByRole('checkbox', { name: month('2026-03') }))

    await waitFor(() => {
      expect(api.dashboard.balanceHistory).toHaveBeenLastCalledWith(
        '2026-04-01',
        undefined,
        ['2026-02-01', '2026-03-01'],
      )
    })
    expect(await selectorButton()).toHaveTextContent(`${t('dashboard.compareMonths')} (2)`)
  })

  it('removes a month from the comparison when it is unchecked', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])

    const { user } = renderDashboard('/?month=2026-04&comparison_month=2026-02&comparison_month=2026-03')
    await user.click(await selectorButton())
    await user.click(await screen.findByRole('checkbox', { name: month('2026-03') }))

    await waitFor(() => {
      expect(api.dashboard.balanceHistory).toHaveBeenLastCalledWith(
        '2026-04-01',
        undefined,
        ['2026-02-01'],
      )
    })
    expect(await selectorButton()).toHaveTextContent(month('2026-02'))
  })

  it('disables the selector when there are no earlier months to compare with', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue([])

    renderDashboard('/?month=2026-04')

    expect(await selectorButton()).toBeDisabled()
  })
})
