import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { useLocation, useNavigate } from 'react-router-dom'

import DashboardPage from '@/pages/dashboard'
import { TooltipProvider } from '@/components/ui/tooltip'
import { formatCompactCurrency, formatCurrency } from '@/lib/format'
import { monthLabel } from '@/lib/month-utils'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, DashboardSummary, SpendingPlan } from '@/types'

const layouts = vi.hoisted(() => ({
  saved: {} as Record<string, { id: string }[] | null>,
  saves: [] as { surface: string; ids: string[]; items: { id: string; span?: number }[] }[],
}))

const api = vi.hoisted(() => ({
  dashboard: {
    summary: vi.fn(),
    spendingByCategory: vi.fn(),
    balanceHistory: vi.fn(),
    balanceHistoryMonths: vi.fn(),
    projectedTransactions: vi.fn(),
  },
  transactions: { list: vi.fn(), calendar: vi.fn(), update: vi.fn(), get: vi.fn() },
  budgets: { comparison: vi.fn() },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn(), listIncludingHidden: vi.fn() },
  accounts: { list: vi.fn() },
  assets: { list: vi.fn() },
  currencies: { list: vi.fn() },
  mortgagePayments: { get: vi.fn() },
  goals: { summary: vi.fn() },
  groups: { list: vi.fn() },
  payees: { list: vi.fn() },
  fiscal: { taxIdKinds: vi.fn() },
  rules: { create: vi.fn(), list: vi.fn() },
  settings: { attachments: vi.fn() },
  spendingPlan: { get: vi.fn(async (): Promise<SpendingPlan | null> => null), timeline: vi.fn() },
  // Nobody arranged anything: Securo's default layout. Tests that need one set `layouts.saved`.
  uiLayouts: {
    get: async () => ({ dashboard: null, reports: null, ...layouts.saved }),
    // Like the server: what was saved is what the next read returns.
    save: async (surface: string, items: { id: string; span?: number }[]) => {
      layouts.saves.push({ surface, ids: items.map((item) => item.id), items })
      layouts.saved = { ...layouts.saved, [surface]: items }
    },
    reset: async (surface: string) => {
      layouts.saved = { ...layouts.saved, [surface]: null }
    },
  },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

const preferences = vi.hoisted(() => ({ currency_display: 'USD' }))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences } }),
}))

const collectionFilter = vi.hoisted(() => ({
  activeAccountIds: null as string[] | null,
  activeWalletIds: null as string[] | null,
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => collectionFilter,
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
      <button onClick={() => navigate('/?month=2026-04&comparison_month=2026-03')}>Compare April with March</button>
    </>
  )
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location-search">{location.search}</output>
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
  layouts.saved = {}
  layouts.saves = []
  preferences.currency_display = 'USD'
  collectionFilter.activeAccountIds = null
  collectionFilter.activeWalletIds = null
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
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.categoryGroups.listIncludingHidden.mockResolvedValue([])
  api.accounts.list.mockResolvedValue(accounts)
  api.assets.list.mockResolvedValue([{ id: 'home', name: 'PR review home' }])
  api.currencies.list.mockResolvedValue([])
  api.mortgagePayments.get.mockResolvedValue({
    payment_transaction_id: 'payment-1',
    payment_amount: 250,
    currency: 'USD',
    payment_account_name: 'Checking',
    allocations: [],
    principal_total: 0,
    interest_total: 0,
    principal_percentage: 0,
    interest_percentage: 0,
  })
  api.goals.summary.mockResolvedValue([])
  api.groups.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
  api.rules.list.mockResolvedValue([])
  api.settings.attachments.mockResolvedValue({ enabled: false })
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

  it('lists mortgage debt as its own row instead of cash', async () => {
    api.accounts.list.mockResolvedValue([
      ...accounts,
      account({ id: 'mortgage', name: 'Mortgage', type: 'loan', current_balance: -40000 }),
    ])
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    await user.hover(screen.getByRole('button', { name: 'i' }))

    const tooltip = await screen.findByRole('tooltip')
    const cashRow = within(tooltip).getByText(t('dashboard.cashBalance')).parentElement!
    expect(cashRow).toHaveTextContent(formatCurrency(1000, 'USD', 'en-US'))
    const loanRow = within(tooltip).getByText(t('dashboard.loanBalance')).parentElement!
    expect(loanRow).toHaveTextContent(formatCurrency(-40000, 'USD', 'en-US'))
  })

  it('shows cash and investment accounts as separate rows from the summary', async () => {
    // The summary amounts differ from the account balances on purpose: the
    // breakdown has to read the summary, so equal numbers would hide a
    // regression where the row silently falls back to the account list.
    api.accounts.list.mockResolvedValue([
      account({ id: 'checking', name: 'Checking', current_balance: 1000 }),
      account({ id: 'broker', name: 'Broker', type: 'investment', current_balance: 8000 }),
    ])
    api.dashboard.summary.mockResolvedValue({
      ...summary,
      cash_balance_primary: 900,
      investment_accounts_primary: 8500,
    })
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    await user.hover(screen.getByRole('button', { name: 'i' }))

    const tooltip = await screen.findByRole('tooltip')
    const cashRow = within(tooltip).getByText(t('dashboard.cashBalance')).parentElement!
    expect(cashRow).toHaveTextContent(formatCurrency(900, 'USD', 'en-US'))
    const investmentRow = within(tooltip).getByText(t('dashboard.investmentAccounts')).parentElement!
    expect(investmentRow).toHaveTextContent(formatCurrency(8500, 'USD', 'en-US'))
  })

  it('keeps the headline on current balances while a past month reads the cutoff', async () => {
    // September ends at 1000 and the user spends 700 since: the headline and
    // the chips have to agree on today's 300, while the breakdown keeps the
    // month-end value that matches total_balance (#959 review).
    api.accounts.list.mockResolvedValue([account({ id: 'checking', name: 'Checking', current_balance: 300 })])
    api.dashboard.summary.mockResolvedValue({
      ...summary,
      cash_balance_primary: 1000,
    })
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
      { route: '/?month=2026-09' },
    )

    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    const heading = screen.getByText(t('dashboard.availableBalance'))
    expect(heading.nextElementSibling).toHaveTextContent(formatCurrency(300, 'USD', 'en-US'))
    const chip = screen.getByText('Checking').closest('a')!
    expect(chip).toHaveTextContent(formatCurrency(300, 'USD', 'en-US'))

    await user.hover(screen.getByRole('button', { name: 'i' }))
    const tooltip = await screen.findByRole('tooltip')
    const cashRow = within(tooltip).getByText(t('dashboard.cashBalance')).parentElement!
    expect(cashRow).toHaveTextContent(formatCurrency(1000, 'USD', 'en-US'))
  })

  it('keeps the investment row visible while the summary is unavailable', async () => {
    api.accounts.list.mockResolvedValue([
      account({ id: 'checking', name: 'Checking', current_balance: 1000 }),
      account({ id: 'broker', name: 'Broker', type: 'investment', current_balance: 8000 }),
    ])
    api.dashboard.summary.mockRejectedValue(new Error('summary unavailable'))
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await screen.findByText('Checking')
    await user.hover(screen.getByRole('button', { name: 'i' }))

    const tooltip = await screen.findByRole('tooltip')
    const investmentRow = within(tooltip).getByText(t('dashboard.investmentAccounts')).parentElement!
    expect(investmentRow).toHaveTextContent(formatCurrency(8000, 'USD', 'en-US'))
  })

  it('shows no account figures for a collection with no accounts', async () => {
    // The summary request carries no account filter in this state and would
    // return workspace-wide totals; account-only figures must stay empty.
    collectionFilter.activeAccountIds = []
    api.accounts.list.mockResolvedValue([
      account({ id: 'checking', name: 'Checking', current_balance: 1000 }),
      account({ id: 'broker', name: 'Broker', type: 'investment', current_balance: 8000 }),
    ])
    api.dashboard.summary.mockResolvedValue({
      ...summary,
      cash_balance_primary: 1000,
      investment_accounts_primary: 8000,
    })
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    const heading = screen.getByText(t('dashboard.availableBalance'))
    expect(heading.nextElementSibling).toHaveTextContent(formatCurrency(0, 'USD', 'en-US'))
    await user.hover(screen.getByRole('button', { name: 'i' }))

    const tooltip = await screen.findByRole('tooltip')
    const cashRow = within(tooltip).getByText(t('dashboard.cashBalance')).parentElement!
    expect(cashRow).toHaveTextContent(formatCurrency(0, 'USD', 'en-US'))
    expect(within(tooltip).queryByText(t('dashboard.investmentAccounts'))).not.toBeInTheDocument()
  })
})

it('shows the mortgage breakdown editor for a payment opened from the dashboard', async () => {
  const loan = account({
    id: 'mortgage',
    name: 'Mortgage',
    type: 'loan',
    secured_asset_id: 'home',
  })
  api.accounts.list.mockResolvedValue([...accounts, loan])
  api.transactions.list.mockResolvedValue({
    items: [
      {
        id: 'payment-1',
        user_id: 'user-1',
        account_id: 'checking',
        category_id: null,
        category: null,
        external_id: null,
        description: 'Mortgage payment',
        original_description: null,
        amount: 250,
        currency: 'USD',
        date: '2026-10-05',
        type: 'debit',
        source: 'manual',
        status: 'posted',
        payee: null,
        payee_id: null,
        payee_name: null,
        notes: null,
        transfer_pair_id: null,
        amount_primary: null,
        fx_rate_used: null,
        fx_fallback: false,
        installment_number: null,
        total_installments: null,
        installment_total_amount: null,
        installment_purchase_date: null,
        installment_series_id: null,
        bill_id: null,
        effective_bill_date: null,
        splits: [],
        is_ignored: false,
      },
    ],
    total: 1,
    page: 1,
    limit: 500,
  })
  const { user } = renderWithProviders(
    <TooltipProvider delayDuration={0}>
      <DashboardPage />
    </TooltipProvider>,
  )

  await user.click(await screen.findByText('Mortgage payment'))
  await user.click(await screen.findByRole('button', { name: t('transactions.mortgageAllocation') }))

  expect(await screen.findByText(/Enter all parts in USD/)).toBeInTheDocument()
  expect(screen.getByLabelText(t('transactions.mortgagePrincipal'))).toBeInTheDocument()
  expect(screen.getByLabelText(t('transactions.mortgageInterest'))).toBeInTheDocument()
})

it('dates card rows by the bill due date the server reports (#1379)', async () => {
  api.transactions.list.mockResolvedValue({
    items: [
      {
        id: 'charge-1',
        user_id: 'user-1',
        account_id: 'card-c',
        category_id: null,
        category: null,
        external_id: null,
        description: 'October charge',
        original_description: null,
        amount: 16.9,
        currency: 'USD',
        date: '2026-10-17',
        reporting_date: '2026-11-11',
        type: 'debit',
        source: 'manual',
        status: 'posted',
        payee: null,
        payee_id: null,
        payee_name: null,
        notes: null,
        transfer_pair_id: null,
        amount_primary: null,
        fx_rate_used: null,
        fx_fallback: false,
        installment_number: null,
        total_installments: null,
        installment_total_amount: null,
        installment_purchase_date: null,
        installment_series_id: null,
        bill_id: null,
        effective_bill_date: null,
        splits: [],
        is_ignored: false,
      },
    ],
    total: 1,
    page: 1,
    limit: 500,
  })
  api.dashboard.projectedTransactions.mockResolvedValue([
    {
      recurring_id: 'rec-1',
      account_id: 'card-c',
      description: 'November charge',
      amount: 16.9,
      amount_primary: null,
      currency: 'USD',
      type: 'debit',
      date: '2026-11-17',
      reporting_date: '2026-12-11',
      category_id: null,
      category_name: null,
      category_icon: null,
      category_color: null,
    },
  ])

  renderWithProviders(
    <TooltipProvider delayDuration={0}>
      <DashboardPage />
    </TooltipProvider>,
  )

  await screen.findAllByText('October charge')
  expect(screen.getAllByText(formatDateEnUs('2026-11-11')).length).toBeGreaterThan(0)
  expect(screen.queryByText(formatDateEnUs('2026-10-17'))).not.toBeInTheDocument()
  await screen.findAllByText('November charge')
  expect(screen.getAllByText(formatDateEnUs('2026-12-11')).length).toBeGreaterThan(0)
  expect(screen.queryByText(formatDateEnUs('2026-11-17'))).not.toBeInTheDocument()
})

function formatDateEnUs(isoDate: string) {
  return new Date(isoDate + 'T00:00:00').toLocaleDateString('en-US')
}

describe('Dashboard available balance with reserves', () => {
  it.each([
    { currency: 'USD', current_balance: 1000, balance_primary: null, expected: 1000 },
    { currency: 'BRL', current_balance: 1000, balance_primary: 200, expected: 200 },
    { currency: 'BRL', current_balance: 0, balance_primary: 0, expected: 0 },
    { currency: 'EUR', current_balance: 1000, balance_primary: null, expected: 1000, fx_fallback: true },
  ])('keeps reserves out of the headline, chip and breakdown: $currency / $current_balance', async (values) => {
    collectionFilter.activeAccountIds = ['reserved', 'card']
    api.accounts.list.mockResolvedValue([
      account({ id: 'reserved', name: 'Reserved account', reserved_total: 4000, ...values }),
      account({ id: 'outside', current_balance: 90000, reserved_total: 10000 }),
      account({ id: 'card', type: 'credit_card', current_balance: -500 }),
    ])
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    const chip = (await screen.findByText('Reserved account')).closest('a')!
    const currency = values.balance_primary == null ? values.currency : 'USD'
    expect(chip).toHaveTextContent(formatCurrency(values.expected, currency, 'en-US'))
    const heading = screen.getByText(t('dashboard.availableBalance'))
    expect(heading.nextElementSibling).toHaveTextContent(formatCurrency(values.expected, 'USD', 'en-US'))
    await user.hover(screen.getByRole('button', { name: 'i' }))
    const tooltip = await screen.findByRole('tooltip')
    const row = within(tooltip).getByText(t('dashboard.cashBalance')).parentElement!
    expect(row).toHaveTextContent(formatCurrency(values.expected, 'USD', 'en-US'))
  })
})

describe('Dashboard available balance without an FX rate', () => {
  const renderDashboard = () =>
    renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

  it('shows an unconverted account in its own currency and flags the total', async () => {
    api.accounts.list.mockResolvedValue([
      account({ id: 'checking', name: 'Checking', current_balance: 1000 }),
      account({
        id: 'eur',
        name: 'Euro Savings',
        type: 'savings',
        currency: 'EUR',
        current_balance: 100,
        fx_fallback: true,
      }),
    ])
    renderDashboard()

    const chip = (await screen.findByText('Euro Savings')).closest('a')!
    expect(chip).toHaveTextContent(formatCurrency(100, 'EUR', 'en-US'))
    expect(screen.getByText(t('transactions.fxFallbackBanner'))).toBeInTheDocument()
  })

  it('does not flag the total when every account is converted', async () => {
    renderDashboard()

    await screen.findByText('Checking')
    expect(screen.queryByText(t('transactions.fxFallbackBanner'))).not.toBeInTheDocument()
  })
})

describe('Dashboard balance comparisons', () => {
  const month = (value: string) => monthLabel(value, 'en')
  const money = (value: number) => formatCurrency(value, 'USD', 'en-US')

  function renderDashboard(route: string) {
    return renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
      { route },
    )
  }

  const selectorButton = (singleMonth?: string) =>
    screen.findByRole('button', {
      name: singleMonth
        ? month(singleMonth)
        : (name) => name === t('dashboard.compareMonths') || name.startsWith(`${t('dashboard.compareMonths')} (`),
    })

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

  function balanceChart(container: HTMLElement) {
    const chart = container.querySelector<HTMLElement>('.recharts-wrapper.cursor-pointer')
    if (!chart) throw new Error('Balance chart was not rendered')
    vi.spyOn(chart, 'getBoundingClientRect').mockReturnValue(DOMRect.fromRect({ width: 800, height: 300 }))
    Object.defineProperties(chart, {
      offsetWidth: { configurable: true, value: 800 },
      offsetHeight: { configurable: true, value: 300 },
    })
    return chart
  }

  it('keeps the balance axis labels compact so a four digit amount fits', async () => {
    // A full amount outgrew the axis gutter and the browser clipped its leading
    // characters (#1051). The ticks are compact; the tooltip keeps the exact sum.
    mockHistory({ current: [{ day: 1, balance: 2500 }] })

    const { container } = renderDashboard('/?month=2026-04')

    const chart = await waitFor(() => balanceChart(container))
    const labels = await waitFor(() => {
      const texts = Array.from(chart.querySelectorAll('.recharts-yAxis-tick-labels text')).map(
        (text) => text.textContent ?? '',
      )
      expect(texts).not.toHaveLength(0)
      return texts
    })

    expect(labels).toContain(formatCompactCurrency(1500, 'USD', 'en-US'))
    // The previous full amounts read "$1,125" or "$1,500", which no longer fit.
    expect(labels.filter((label) => label.includes(','))).toEqual([])
  })

  it('labels a single comparison month in the selector, legend and footer', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])
    // Current month is +100 since its opening, February was +50: 50 ahead.
    mockHistory({
      comparison: [
        { day: 1, balance: 1950 },
        { day: 2, balance: 1950 },
      ],
      comparison_month: '2026-02',
      comparison_opening_balance: 1900,
    })

    renderDashboard('/?month=2026-04&comparison_month=2026-02')

    expect(
      await screen.findByText('Day 1: cumulative change is $50.00 above February 2026 ($50.00).'),
    ).toBeInTheDocument()
    expect(await selectorButton('2026-02')).toHaveTextContent(month('2026-02'))
    // The legend shows the month itself, not the median wording.
    expect(screen.getAllByText(month('2026-02')).some((el) => !el.closest('button'))).toBe(true)
    expect(screen.queryByText(t('dashboard.balanceMedianLegend'))).not.toBeInTheDocument()
    expect(api.dashboard.balanceHistory).toHaveBeenCalledWith('2026-04-01', undefined, ['2026-02-01'])
  })

  it('does not carry a comparison month past its last known day', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])
    // The comparison month is still in progress: days after today come back null.
    mockHistory({
      current: [
        { day: 1, balance: 1100 },
        { day: 2, balance: 1100 },
      ],
      comparison: [
        { day: 1, balance: 1950 },
        { day: 2, balance: null },
      ],
      comparison_month: '2026-03',
      comparison_opening_balance: 1900,
    })

    const { container } = renderDashboard('/?month=2026-04&comparison_month=2026-03')

    await waitFor(async () => expect(await selectorButton('2026-03')).toHaveTextContent(month('2026-03')))
    expect(await screen.findAllByText(month('2026-03'))).not.toHaveLength(0)
    // Day 2 has no comparison value, but its footer space is still reserved.
    const placeholder = container.querySelector('.h-20.shrink-0')
    expect(placeholder).not.toBeNull()
    expect(placeholder).toBeEmptyDOMElement()
  })

  it('keeps the URL selection when the month options fail to load', async () => {
    api.dashboard.balanceHistoryMonths.mockRejectedValue(new Error('network'))

    renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <LocationProbe />
        <DashboardPage />
      </TooltipProvider>,
      { route: '/?month=2026-04&comparison_month=2026-02' },
    )

    expect(await screen.findByRole('heading', { name: 'April 2026' })).toBeInTheDocument()
    // History waits for the month options, so once it is requested the failed request has settled.
    await waitFor(() => expect(api.dashboard.balanceHistory).toHaveBeenCalled())
    await act(() => new Promise((resolve) => setTimeout(resolve, 0)))
    expect(screen.getByTestId('location-search')).toHaveTextContent('comparison_month=2026-02')
  })

  it('shows the median and the range with three months, compared against the median', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])
    mockHistory({
      baseline: [{ day: 1, median: 40, lower_quartile: 20, upper_quartile: 60 }],
    })

    renderDashboard('/?month=2026-04&comparison_month=2026-01&comparison_month=2026-02&comparison_month=2026-03')

    expect(await screen.findByText('Day 1: cumulative change is $60.00 above the median ($40.00).')).toBeInTheDocument()
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

  it.each(['median', 'single month'])('keeps the %s footer on the hovered chart day', async (mode) => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])
    const singleMonth = mode === 'single month'
    mockHistory({
      current: [
        { day: 1, balance: 1100 },
        { day: 2, balance: 1200 },
        { day: 3, balance: 5000 },
        { day: 4, balance: null },
      ],
      ...(singleMonth
        ? {
            comparison: [
              { day: 1, balance: 1940 },
              { day: 2, balance: 2050 },
              { day: 3, balance: 2400 },
              { day: 4, balance: 2600 },
            ],
            comparison_month: '2026-02',
            comparison_opening_balance: 1900,
          }
        : {
            baseline: [
              { day: 1, median: 40, lower_quartile: 20, upper_quartile: 60 },
              { day: 2, median: 150, lower_quartile: 100, upper_quartile: 200 },
              { day: 3, median: 500, lower_quartile: 400, upper_quartile: 600 },
              { day: 4, median: 700, lower_quartile: 600, upper_quartile: 800 },
            ],
          }),
    })
    const footer = (day: number, delta: number, median: number) =>
      t(singleMonth ? 'dashboard.balanceFlowVsMonth' : 'dashboard.balanceFlowVsMedian', {
        context: 'above',
        day,
        delta: money(delta),
        median: money(median),
        month: month('2026-02'),
      })
    const { container } = renderDashboard(
      singleMonth
        ? '/?month=2026-04&comparison_month=2026-02'
        : '/?month=2026-04&comparison_month=2026-01&comparison_month=2026-02&comparison_month=2026-03',
    )

    expect(await screen.findByText(footer(3, 3500, 500))).toBeInTheDocument()
    const chart = balanceChart(container)

    // Exercise Recharts itself: its first two categorical indexes are "0" and "1".
    fireEvent.mouseMove(chart, { clientX: 64, clientY: 100 })
    expect(await within(chart).findByText(money(100))).toBeInTheDocument()
    expect(within(chart).getByText(money(40))).toBeInTheDocument()
    expect(await screen.findByText(footer(1, 60, 40))).toBeInTheDocument()

    fireEvent.mouseMove(chart, { clientX: 89, clientY: 100 })
    expect(await within(chart).findByText(money(200))).toBeInTheDocument()
    expect(within(chart).getByText(money(150))).toBeInTheDocument()
    expect(await screen.findByText(footer(2, 50, 150))).toBeInTheDocument()

    const footerContainer = screen.getByText(footer(2, 50, 150)).parentElement
    expect(footerContainer).toHaveClass('h-20', 'shrink-0')
    fireEvent.mouseMove(chart, { clientX: 139, clientY: 100 })
    expect(await within(chart).findByText(money(700))).toBeInTheDocument()
    expect(within(chart).queryByText('April', { selector: '.recharts-tooltip-item-name' })).not.toBeInTheDocument()
    await waitFor(() => expect(footerContainer).toBeEmptyDOMElement())
    expect(footerContainer).toBeInTheDocument()
    expect(footerContainer).toHaveClass('h-20', 'shrink-0')

    // Moving outside the plot yields a null index, not day one's index zero.
    fireEvent.mouseMove(chart, { clientX: 0, clientY: 0 })
    expect(await screen.findByText(footer(3, 3500, 500))).toBeInTheDocument()

    fireEvent.mouseMove(chart, { clientX: 64, clientY: 100 })
    expect(await screen.findByText(footer(1, 60, 40))).toBeInTheDocument()
    fireEvent.mouseLeave(chart)
    expect(await screen.findByText(footer(3, 3500, 500))).toBeInTheDocument()
  })

  it.each([
    { current: 100, median: 150, color: 'var(--chart-income)', comparison: '$50.00 below the median ($150.00)' },
    { current: -100, median: -150, color: 'var(--chart-expense)', comparison: '$50.00 above the median (-$150.00)' },
    { current: 0, median: 0, color: 'var(--muted-foreground)', comparison: 'equals the median ($0.00)' },
  ])(
    'colors the tooltip by cumulative change $current, not its difference from the median',
    async ({ current, median, color, comparison }) => {
      api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-02', '2026-03'])
      mockHistory({
        current: [{ day: 1, balance: 1000 + current }],
        baseline: [{ day: 1, median, lower_quartile: null, upper_quartile: null }],
      })
      const { container } = renderDashboard('/?month=2026-04&comparison_month=2026-02&comparison_month=2026-03')

      const footer = await screen.findByText(
        `Day 1: cumulative change ${current === median ? '' : 'is '}${comparison}.`,
      )
      const triangle = footer.querySelector('span')
      if (current === median) {
        expect(triangle).toBeNull()
      } else {
        expect(triangle).toHaveTextContent(current > median ? '\u25B2' : '\u25BC')
        expect(triangle).toHaveClass(current > median ? 'text-emerald-600' : 'text-rose-500')
        expect(triangle).toHaveAttribute('aria-hidden', 'true')
      }
      const chart = balanceChart(container)
      fireEvent.mouseMove(chart, { clientX: 64, clientY: 100 })

      const label = await within(chart).findByText('April', { selector: '.recharts-tooltip-item-name' })
      expect(label.closest('li')).toHaveStyle({ color })
      const medianLabel = within(chart).getByText(t('dashboard.balanceMedianLegend'), {
        selector: '.recharts-tooltip-item-name',
      })
      expect(medianLabel.closest('li')).toHaveStyle({ color: 'var(--chart-muted)' })
    },
  )

  it.each([
    { current: -100, comparison: -100, expected: 'equals February 2026 (-$100.00)' },
    { current: 0, comparison: 50, expected: 'is $50.00 below February 2026 ($50.00)' },
  ])('explains a single-month comparison for cumulative change $current', async ({ current, comparison, expected }) => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-02'])
    mockHistory({
      current: [{ day: 1, balance: 1000 + current }],
      comparison: [{ day: 1, balance: 1900 + comparison }],
      comparison_month: '2026-02',
      comparison_opening_balance: 1900,
    })
    renderDashboard('/?month=2026-04&comparison_month=2026-02')

    expect(await screen.findByText(`Day 1: cumulative change ${expected}.`)).toBeInTheDocument()
  })

  it.each(['median', 'single month'])('uses currency display precision for the %s footer direction', async (mode) => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-02', '2026-03'])
    const singleMonth = mode === 'single month'
    const { queryClient, container } = renderDashboard(
      singleMonth
        ? '/?month=2026-04&comparison_month=2026-02'
        : '/?month=2026-04&comparison_month=2026-02&comparison_month=2026-03',
    )
    await waitFor(() => expect(api.dashboard.balanceHistory).toHaveBeenCalled())

    for (const [currency, unit] of [
      ['USD', 0.01],
      ['JPY', 1],
      ['KWD', 0.001],
    ] as const) {
      preferences.currency_display = currency
      for (const multiplier of [-0.6, -0.4, 0.4, 0.6]) {
        const delta = unit * multiplier
        mockHistory({
          opening_balance: 0,
          current: [{ day: 1, balance: delta }],
          ...(singleMonth
            ? {
                comparison: [{ day: 1, balance: 0 }],
                comparison_month: '2026-02',
                comparison_opening_balance: 0,
              }
            : {
                baseline: [{ day: 1, median: 0, lower_quartile: null, upper_quartile: null }],
              }),
        })
        await act(() => queryClient.refetchQueries({ queryKey: ['dashboard', 'balance-history'] }))
        const equal = Math.abs(multiplier) < 0.5
        const reference = singleMonth ? 'February 2026' : 'the median'
        const formatted = formatCurrency(Math.abs(delta), currency, 'en-US').replace(/\u00a0/g, ' ')
        const zero = formatCurrency(0, currency, 'en-US').replace(/\u00a0/g, ' ')
        const footer = await screen.findByText(
          equal
            ? `Day 1: cumulative change equals ${reference} (${zero}).`
            : `Day 1: cumulative change is ${formatted} ${delta > 0 ? 'above' : 'below'} ${reference} (${zero}).`,
        )
        const triangle = footer.querySelector('span')
        if (equal) {
          expect(triangle).toBeNull()
        } else {
          expect(triangle).toHaveTextContent(delta > 0 ? '\u25B2' : '\u25BC')
          expect(triangle).toHaveClass(delta > 0 ? 'text-emerald-600' : 'text-rose-500')
        }
        expect(container.querySelector('.h-20.shrink-0')).toContainElement(footer)
      }
    }
  })

  it('defaults to the active months of the previous 12 when the URL has no selection', async () => {
    // 2025-03 is more than 12 months before April 2026, 2026-05 is after it.
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2025-03', '2025-06', '2026-03', '2026-05'])

    renderDashboard('/?month=2026-04')

    // The options load after first paint, so the label settles asynchronously.
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: (name) => name.startsWith(t('dashboard.compareMonths')) }),
      ).toHaveTextContent(`${t('dashboard.compareMonths')} (2)`)
    })
    await waitFor(() => {
      expect(api.dashboard.balanceHistory).toHaveBeenCalledWith('2026-04-01', undefined, ['2025-06-01', '2026-03-01'])
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

  it('keeps a comparison month that is only valid for the month being navigated to', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])

    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <LocationProbe />
        <DashboardRouteNavigation />
        <DashboardPage />
      </TooltipProvider>,
      { route: '/?month=2026-03&comparison_month=2026-02' },
    )

    expect(await screen.findByRole('heading', { name: 'March 2026' })).toBeInTheDocument()
    await waitFor(() => expect(api.dashboard.balanceHistory).toHaveBeenCalled())
    // March is not an earlier month while March is still shown, but it is for April.
    await user.click(screen.getByRole('button', { name: 'Compare April with March' }))

    expect(await screen.findByRole('heading', { name: 'April 2026' })).toBeInTheDocument()
    await waitFor(() =>
      expect(api.dashboard.balanceHistory).toHaveBeenCalledWith('2026-04-01', undefined, ['2026-03-01']),
    )
    expect(screen.getByTestId('location-search')).toHaveTextContent('comparison_month=2026-03')
  })

  it('offers only earlier months, newest first, and keeps the last selected month checked', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03', '2026-04', '2026-05'])

    const { user } = renderDashboard('/?month=2026-04&comparison_month=2026-02')
    await user.click(await selectorButton('2026-02'))

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
    await user.click(await selectorButton('2026-02'))
    await user.click(await screen.findByRole('checkbox', { name: month('2026-03') }))

    await waitFor(() => {
      expect(api.dashboard.balanceHistory).toHaveBeenLastCalledWith('2026-04-01', undefined, [
        '2026-02-01',
        '2026-03-01',
      ])
    })
    expect(await selectorButton()).toHaveTextContent(`${t('dashboard.compareMonths')} (2)`)
  })

  it('removes a month from the comparison when it is unchecked', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-01', '2026-02', '2026-03'])

    const { user } = renderDashboard('/?month=2026-04&comparison_month=2026-02&comparison_month=2026-03')
    await user.click(await selectorButton())
    await user.click(await screen.findByRole('checkbox', { name: month('2026-03') }))

    await waitFor(() => {
      expect(api.dashboard.balanceHistory).toHaveBeenLastCalledWith('2026-04-01', undefined, ['2026-02-01'])
    })
    expect(await selectorButton('2026-02')).toHaveTextContent(month('2026-02'))
  })

  it('disables the selector when there are no earlier months to compare with', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue([])

    renderDashboard('/?month=2026-04')

    expect(await selectorButton()).toBeDisabled()
  })
})

describe('Dashboard net worth without an FX rate', () => {
  const renderDashboard = () =>
    renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

  it('flags the net worth total when the summary carried a fallback', async () => {
    api.dashboard.summary.mockResolvedValue({ ...summary, fx_fallback: true })
    renderDashboard()

    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    expect(screen.getByText(t('transactions.fxFallbackBanner'))).toBeInTheDocument()
  })

  it('keeps the net worth quiet when every bucket was converted', async () => {
    renderDashboard()

    await screen.findByText('Checking')
    expect(screen.queryByText(t('transactions.fxFallbackBanner'))).not.toBeInTheDocument()
  })
})

describe('Dashboard refresh button', () => {
  it('refetches page data when the refresh button is clicked', async () => {
    const { user, queryClient } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    const refetch = vi.spyOn(queryClient, 'refetchQueries')

    // The button waits out the first load, which follows the saved layout.
    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    const button = screen.getByRole('button', { name: 'Refresh' })
    await waitFor(() => expect(button).toBeEnabled())
    await user.click(button)

    expect(refetch).toHaveBeenCalledTimes(1)
    // Only the queries on screen: a refresh must not refetch every page visited earlier.
    expect(refetch).toHaveBeenCalledWith({ type: 'active' })
  })

  it('refreshes comparison data without losing the selected months', async () => {
    api.dashboard.balanceHistoryMonths.mockResolvedValue(['2026-02', '2026-03'])
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <LocationProbe />
        <DashboardPage />
      </TooltipProvider>,
      { route: '/?month=2026-04&comparison_month=2026-02' },
    )

    await waitFor(() =>
      expect(api.dashboard.balanceHistory).toHaveBeenLastCalledWith('2026-04-01', undefined, ['2026-02-01']),
    )
    const historyCalls = api.dashboard.balanceHistory.mock.calls.length
    const monthOptionsCalls = api.dashboard.balanceHistoryMonths.mock.calls.length

    await user.click(screen.getByRole('button', { name: 'Refresh' }))

    await waitFor(() => {
      expect(api.dashboard.balanceHistory.mock.calls.length).toBeGreaterThan(historyCalls)
      expect(api.dashboard.balanceHistoryMonths.mock.calls.length).toBeGreaterThan(monthOptionsCalls)
    })
    expect(api.dashboard.balanceHistory).toHaveBeenLastCalledWith('2026-04-01', undefined, ['2026-02-01'])
    expect(screen.getByTestId('location-search')).toHaveTextContent('comparison_month=2026-02')
  })
})

it('keeps a wallet-only Expenses drill-down empty instead of querying all accounts', async () => {
  collectionFilter.activeAccountIds = []
  collectionFilter.activeWalletIds = ['wallet-1']
  const { user } = renderWithProviders(
    <TooltipProvider>
      <DashboardPage />
    </TooltipProvider>,
  )
  await user.click(await screen.findByRole('button', { name: /Expenses/ }))
  const panel = screen.getByRole('complementary')
  expect(await within(panel).findByText(t('dashboard.drillDownEmpty'))).toBeInTheDocument()
  expect(api.transactions.list.mock.calls.some(([params]) => params?.user_pnl_only === true)).toBe(false)
})

it('lists only the user share of a split credit in the Income drill-down, like the card', async () => {
  api.transactions.list.mockResolvedValue({
    items: [
      {
        id: 'shared-refund',
        description: 'Shared refund',
        date: '2026-11-05',
        type: 'credit',
        amount: 100,
        amount_primary: 100,
        currency: 'USD',
        status: 'posted',
        category: null,
        attachment_count: 0,
        splits: [],
        is_shared: true,
        viewer_share: 40,
        user_id: 'friend',
      },
    ],
    total: 1,
  })
  const { user } = renderWithProviders(
    <TooltipProvider>
      <DashboardPage />
    </TooltipProvider>,
  )
  await user.click(await screen.findByRole('button', { name: /Income/ }))
  const panel = screen.getByRole('complementary')
  expect(await within(panel).findByText('+$40.00')).toBeInTheDocument()
  expect(within(panel).getByText(/part of \$100\.00/)).toBeInTheDocument()
})

it('switches the Income drill-down to the user share when the collection is cleared while it is open', async () => {
  collectionFilter.activeAccountIds = ['checking']
  api.transactions.list.mockResolvedValue({
    items: [
      {
        id: 'shared-refund',
        description: 'Shared refund',
        date: '2026-11-05',
        type: 'credit',
        amount: 100,
        amount_primary: 100,
        currency: 'USD',
        status: 'posted',
        category: null,
        attachment_count: 0,
        splits: [],
        is_shared: true,
        viewer_share: 40,
        user_id: 'friend',
      },
    ],
    total: 1,
  })
  const { user, rerender } = renderWithProviders(
    <TooltipProvider>
      <DashboardPage />
    </TooltipProvider>,
  )
  await user.click(await screen.findByRole('button', { name: /Income/ }))
  expect(await within(screen.getByRole('complementary')).findByText('+$100.00')).toBeInTheDocument()

  collectionFilter.activeAccountIds = null
  rerender(
    <TooltipProvider>
      <DashboardPage />
    </TooltipProvider>,
  )
  expect(await within(screen.getByRole('complementary')).findByText('+$40.00')).toBeInTheDocument()
})

it('identifies each category in its spending progress bar accessible name', async () => {
  api.dashboard.spendingByCategory.mockResolvedValue([
    { category_id: 'rent', category_name: 'Rent', projected_total: 450, total: 450 },
    { category_id: 'food', category_name: 'Groceries', projected_total: 200, total: 200 },
  ])
  api.budgets.comparison.mockResolvedValue([
    { category_id: 'rent', budget_amount: 900, percentage_used: 50, projected_prev_month_amount: 0 },
    { category_id: 'food', budget_amount: 500, percentage_used: 40, projected_prev_month_amount: 0 },
  ])
  renderWithProviders(
    <TooltipProvider>
      <DashboardPage />
    </TooltipProvider>,
  )

  expect(
    await screen.findByRole('progressbar', { name: `${t('dashboard.spendingByCategory')}: Rent` }),
  ).toHaveAttribute('aria-valuenow', '50')
  expect(screen.getByRole('progressbar', { name: `${t('dashboard.spendingByCategory')}: Groceries` })).toHaveAttribute(
    'aria-valuenow',
    '40',
  )
})

describe('Dashboard arranged from the library', () => {
  const regions = () => screen.getAllByRole('region').map((region) => region.getAttribute('aria-label'))

  it("shows Securo's arrangement when nothing was saved", async () => {
    renderWithProviders(
      <TooltipProvider>
        <DashboardPage />
      </TooltipProvider>,
    )
    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    expect(regions()).toEqual([
      t('library.widget.overview'),
      t('library.widget.uncategorized'),
      t('dashboard.spendingByCategory'),
      t('dashboard.balanceFlow'),
      t('dashboard.periodTransactions'),
    ])
  })

  it('follows the saved order and loads nothing for a widget left out', async () => {
    layouts.saved = { dashboard: [{ id: 'transactions' }, { id: 'overview' }] }
    renderWithProviders(
      <TooltipProvider>
        <DashboardPage />
      </TooltipProvider>,
    )
    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    await waitFor(() => expect(regions()).toEqual([t('dashboard.periodTransactions'), t('library.widget.overview')]))
    expect(api.goals.summary).not.toHaveBeenCalled()
    expect(api.dashboard.balanceHistory).not.toHaveBeenCalled()
    expect(api.dashboard.balanceHistoryMonths).not.toHaveBeenCalled()
    expect(api.dashboard.spendingByCategory).not.toHaveBeenCalled()
    expect(api.budgets.comparison).not.toHaveBeenCalled()
  })

  it("leaves out a widget this workspace can't run, and keeps it in the saved arrangement", async () => {
    // Saved where invoices are shown; this workspace doesn't show them.
    layouts.saved = { dashboard: [{ id: 'overview' }, { id: 'receivables' }, { id: 'transactions' }] }
    const { user } = renderWithProviders(
      <TooltipProvider>
        <DashboardPage />
      </TooltipProvider>,
    )
    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    await waitFor(() => expect(regions()).toEqual([t('library.widget.overview'), t('dashboard.periodTransactions')]))

    await user.click(screen.getByRole('button', { name: t('library.customizeDashboard') }))
    expect(regions()).not.toContain(t('widgets.receivables.title'))
    await user.click(
      screen.getByRole('button', { name: t('library.removeFrom.dashboard', { name: t('library.widget.overview') }) }),
    )
    await waitFor(() => expect(layouts.saves.at(-1)?.ids).toEqual(['transactions', 'receivables']))
  })

  it('saves a widget resized while customizing', async () => {
    const { user } = renderWithProviders(
      <TooltipProvider>
        <DashboardPage />
      </TooltipProvider>,
    )
    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    await user.click(screen.getByRole('button', { name: t('library.customizeDashboard') }))
    expect(screen.getByText(t('library.editingDashboard'))).toBeInTheDocument()

    await user.click(
      screen.getByRole('button', { name: t('library.widthOf', { name: t('dashboard.spendingByCategory') }) }),
    )
    await user.click(screen.getByRole('menuitemradio', { name: t('library.width.8') }))
    await waitFor(() => expect(layouts.saves.at(-1)?.items).toContainEqual({ id: 'spending-by-category', span: 8 }))
    expect(screen.getByRole('region', { name: t('dashboard.spendingByCategory') })).toHaveClass('lg:col-span-8')

    await user.click(screen.getByRole('button', { name: t('library.done') }))
    expect(screen.queryByText(t('library.editingDashboard'))).not.toBeInTheDocument()
  })
})

describe('Dashboard daily plan strip', () => {
  afterEach(() => {
    api.spendingPlan.get.mockResolvedValue(null)
  })

  const renderDashboard = () =>
    renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

  it('shows what is left to spend today when a plan exists', async () => {
    api.spendingPlan.get.mockResolvedValue({
      daily_allowance: '30.00',
      balance_floor: '0.00',
      account_ids: null,
      horizon_months: 12,
    })
    api.spendingPlan.timeline.mockResolvedValue({
      currency: 'USD',
      daily_allowance: 30,
      balance_floor: 0,
      horizon_end: '2027-11-12',
      safe_daily_allowance: 40,
      average_daily_surplus: 50,
      covered_until: '2027-11-12',
      covered_through_horizon: true,
      today: { allowance: 30, spent: 10, remaining: 20 },
      points: [],
    })
    renderDashboard()

    const strip = (
      await screen.findByText(t('spendingPlan.todayRemaining', { amount: formatCurrency(20, 'USD', 'en-US') }))
    ).closest('a')!
    expect(strip).toHaveAttribute('href', '/plan')
  })

  it('leaves the strip out without a plan', async () => {
    renderDashboard()

    await screen.findByText('Checking')
    expect(api.spendingPlan.timeline).not.toHaveBeenCalled()
    expect(document.querySelector('a[href="/plan"]')).toBeNull()
  })
})

describe('Dashboard uncategorized drill-down', () => {
  const uncategorized = (id: string, description: string, date: string) => ({
    id,
    description,
    date,
    account_id: 'checking',
    category_id: null,
    category: null,
    payee: null,
    payee_id: null,
    amount: 12,
    amount_primary: 12,
    currency: 'USD',
    type: 'debit',
    status: 'posted',
    source: 'manual',
    notes: null,
    attachment_count: 0,
    splits: [],
    viewer_share: null,
    is_shared: false,
    user_id: 'user-1',
    transfer_pair_id: null,
    is_ignored: false,
    tags: [],
  })

  it('saves a row and loads the next one into the open dialog with Save & Next', async () => {
    api.dashboard.summary.mockResolvedValue({
      ...summary,
      pending_categorization: 2,
      pending_categorization_amount: 24,
    })
    api.transactions.list.mockImplementation(async (params?: { uncategorized?: boolean }) =>
      params?.uncategorized
        ? {
            items: [
              uncategorized('first', 'First uncategorized', '2026-09-01'),
              uncategorized('second', 'Second uncategorized', '2026-09-02'),
            ],
            total: 2,
          }
        : { items: [], total: 0 },
    )
    api.transactions.update.mockResolvedValue({})
    api.transactions.get.mockImplementation(async (id: string) =>
      id === 'first'
        ? uncategorized('first', 'First uncategorized', '2026-09-01')
        : uncategorized('second', 'Second uncategorized', '2026-09-02'),
    )

    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
      { route: '/?month=2026-09' },
    )

    await user.click(await screen.findByText(t('dashboard.categorizeNow'), { exact: false }))
    const open = await screen.findAllByRole('button', { name: t('dashboard.drillDownOpenTransaction') })
    await user.click(open[0])

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByDisplayValue('First uncategorized')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: t('transactions.saveAndNext') }))

    await waitFor(() => expect(api.transactions.update).toHaveBeenCalledTimes(1))
    const [savedId, payload] = api.transactions.update.mock.calls[0]
    expect(savedId).toBe('first')
    expect(payload).not.toHaveProperty('action')

    // Same dialog, now on the next row; it was the last one, so no next again.
    const next = await screen.findByRole('dialog')
    expect(await within(next).findByDisplayValue('Second uncategorized')).toBeInTheDocument()
    expect(within(next).queryByRole('button', { name: t('transactions.saveAndNext') })).not.toBeInTheDocument()
  })

  it('re-reads the next row and skips it once a save has categorized it', async () => {
    // Saving the first installment for the whole series also categorizes the second.
    const rows = [
      uncategorized('first', 'Phone 1/2', '2026-09-01'),
      uncategorized('second', 'Phone 2/2', '2026-09-02'),
      uncategorized('third', 'Third uncategorized', '2026-09-03'),
    ]
    api.dashboard.summary.mockResolvedValue({
      ...summary,
      pending_categorization: 3,
      pending_categorization_amount: 36,
    })
    api.transactions.list.mockImplementation(async (params?: { uncategorized?: boolean }) =>
      params?.uncategorized ? { items: rows, total: rows.length } : { items: [], total: 0 },
    )
    api.transactions.update.mockResolvedValue({})
    api.transactions.get.mockImplementation(async (id: string) => {
      const row = rows.find((r) => r.id === id)!
      return id === 'second' && api.transactions.update.mock.calls.length > 0
        ? { ...row, category_id: 'cat-phone' }
        : row
    })

    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
      { route: '/?month=2026-09' },
    )

    await user.click(await screen.findByText(t('dashboard.categorizeNow'), { exact: false }))
    const open = await screen.findAllByRole('button', { name: t('dashboard.drillDownOpenTransaction') })
    await user.click(open[0])

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByDisplayValue('Phone 1/2')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: t('transactions.saveAndNext') }))

    const next = await screen.findByRole('dialog')
    expect(await within(next).findByDisplayValue('Third uncategorized')).toBeInTheDocument()
    expect(api.transactions.get).toHaveBeenCalledWith('second')
  })

  async function openPanel(rows: ReturnType<typeof uncategorized>[]) {
    api.dashboard.summary.mockResolvedValue({
      ...summary,
      pending_categorization: rows.length,
      pending_categorization_amount: 12 * rows.length,
    })
    api.transactions.list.mockImplementation(async (params?: { uncategorized?: boolean }) =>
      params?.uncategorized ? { items: rows, total: rows.length } : { items: [], total: 0 },
    )
    api.transactions.update.mockResolvedValue({})
    const utils = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
      { route: '/?month=2026-09' },
    )
    await utils.user.click(await screen.findByText(t('dashboard.categorizeNow'), { exact: false }))
    const open = await screen.findAllByRole('button', { name: t('dashboard.drillDownOpenTransaction') })
    return { ...utils, open }
  }

  it('skips a next row that was ignored after the panel loaded', async () => {
    const rows = [
      uncategorized('first', 'First row', '2026-09-01'),
      uncategorized('second', 'Second row', '2026-09-02'),
      uncategorized('third', 'Third row', '2026-09-03'),
    ]
    api.transactions.get.mockImplementation(async (id: string) => {
      const row = rows.find((r) => r.id === id)!
      return id === 'second' ? { ...row, is_ignored: true } : row
    })
    const { user, open } = await openPanel(rows)

    await user.click(open[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: t('transactions.saveAndNext') }))

    expect(await within(await screen.findByRole('dialog')).findByDisplayValue('Third row')).toBeInTheDocument()
  })

  it('stops at a next row that fails to load, leaving it in the panel', async () => {
    const rows = [
      uncategorized('first', 'First row', '2026-09-01'),
      uncategorized('second', 'Second row', '2026-09-02'),
      uncategorized('third', 'Third row', '2026-09-03'),
    ]
    api.transactions.get.mockImplementation(async (id: string) => {
      if (id === 'second') throw { response: { status: 503 } }
      return rows.find((r) => r.id === id)
    })
    const { user, open } = await openPanel(rows)

    await user.click(open[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: t('transactions.saveAndNext') }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    // Not skipped past: the third row was never loaded into the dialog.
    expect(api.transactions.get).not.toHaveBeenCalledWith('third')
    expect(api.transactions.update).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Second row')).toBeInTheDocument()
  })

  it('opens the row clicked last even when an earlier click loads later', async () => {
    const rows = [
      uncategorized('first', 'First row', '2026-09-01'),
      uncategorized('second', 'Second row', '2026-09-02'),
    ]
    let finishFirst: (tx: unknown) => void = () => {}
    api.transactions.get.mockImplementation((id: string) =>
      id === 'first'
        ? new Promise((resolve) => {
            finishFirst = resolve
          })
        : Promise.resolve(rows[1]),
    )
    const { user, open } = await openPanel(rows)

    await user.click(open[0])
    await user.click(open[1])
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByDisplayValue('Second row')).toBeInTheDocument()

    await act(async () => finishFirst(rows[0]))
    expect(within(screen.getByRole('dialog')).getByDisplayValue('Second row')).toBeInTheDocument()
  })

  it('keeps a row opened after closing the dialog when an earlier Save & next finishes late', async () => {
    const rows = [
      uncategorized('first', 'First row', '2026-09-01'),
      uncategorized('second', 'Second row', '2026-09-02'),
      uncategorized('third', 'Third row', '2026-09-03'),
    ]
    let finishSecond: (tx: unknown) => void = () => {}
    api.transactions.get.mockImplementation((id: string) =>
      id === 'second'
        ? new Promise((resolve) => {
            finishSecond = resolve
          })
        : Promise.resolve(rows.find((r) => r.id === id)),
    )
    const { user, open } = await openPanel(rows)

    await user.click(open[0])
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: t('transactions.saveAndNext') }))
    await waitFor(() => expect(api.transactions.get).toHaveBeenCalledWith('second'))

    // Leave while the next row is still loading, and open another one.
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(screen.getAllByRole('button', { name: t('dashboard.drillDownOpenTransaction') })[2])
    const third = await screen.findByRole('dialog')
    expect(await within(third).findByDisplayValue('Third row')).toBeInTheDocument()
    // The abandoned lookup doesn't keep the new dialog busy.
    expect(within(third).getByRole('button', { name: t('common.save') })).toBeEnabled()

    await act(async () => finishSecond(rows[1]))
    expect(within(screen.getByRole('dialog')).getByDisplayValue('Third row')).toBeInTheDocument()
  })
})
