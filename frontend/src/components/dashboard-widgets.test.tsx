import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import {
  AssistantWidget,
  CreditCardsWidget,
  MonthResultWidget,
  SpendingDistributionWidget,
  SpendingPaceWidget,
  UpcomingWidget,
  type DashboardScope,
} from '@/components/dashboard-widgets'
import { AssistantContext } from '@/contexts/assistant-context'
import { formatCurrency } from '@/lib/format'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, DashboardSummary } from '@/types'

const api = vi.hoisted(() => ({
  dashboard: { summary: vi.fn(), spendingByCategory: vi.fn(), projectedTransactions: vi.fn() },
  budgets: { comparison: vi.fn() },
  accounts: { list: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/hooks/use-privacy-mode', () => ({ usePrivacyMode: () => ({ mask: (v: string) => v, privacyMode: false, MASK: '••••' }) }))

const usd = (value: number) => formatCurrency(value, 'USD', 'en-US')
const PAST: DashboardScope = { month: '2026-03', activeAccountIds: null, activeWalletIds: null, noAccounts: false, currency: 'USD' }

function summary(income: number, expenses: number): DashboardSummary {
  return { monthly_income_primary: income, monthly_expenses_primary: expenses } as DashboardSummary
}

function account(overrides: Partial<Account>): Account {
  const base = {
    id: 'a', name: 'A', display_name: null, masked_number: null, type: 'checking', current_balance: 0,
    balance_primary: null, currency: 'USD', is_closed: false, shared_balance_group: null, next_due_date: null,
    institution_logo_url: null, institution_name: null,
  }
  return { ...base, ...overrides } as unknown as Account
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('SpendingPaceWidget', () => {
  it('compares a past month with the sum of its budgets', async () => {
    api.dashboard.summary.mockResolvedValue(summary(0, 750))
    api.budgets.comparison.mockResolvedValue([{ budget_amount: 600 }, { budget_amount: 400 }, { budget_amount: null }])
    renderWithProviders(<SpendingPaceWidget scope={PAST} />)
    expect(await screen.findByText(usd(750))).toBeInTheDocument()
    expect(screen.getByText(`/ ${usd(1000)}`)).toBeInTheDocument()
    expect(screen.getByText(t('widgets.spendingPace.left', { amount: usd(250) }))).toBeInTheDocument()
  })

  it('says by how much the budget was passed', async () => {
    api.dashboard.summary.mockResolvedValue(summary(0, 1200))
    api.budgets.comparison.mockResolvedValue([{ budget_amount: 1000 }])
    renderWithProviders(<SpendingPaceWidget scope={PAST} />)
    expect(await screen.findByText(t('widgets.spendingPace.over', { amount: usd(200) }))).toBeInTheDocument()
  })

  it("says the budgets couldn't load rather than that there are none", async () => {
    api.dashboard.summary.mockResolvedValue(summary(0, 300))
    api.budgets.comparison.mockRejectedValue(new Error('offline'))
    renderWithProviders(<SpendingPaceWidget scope={PAST} />)
    expect(await screen.findByText(t('widgets.loadFailed'), {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.queryByText(t('widgets.spendingPace.noBudget'))).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('common.retry') })).toBeInTheDocument()
  })

  it('asks for budgets when there are none', async () => {
    api.dashboard.summary.mockResolvedValue(summary(0, 300))
    api.budgets.comparison.mockResolvedValue([])
    renderWithProviders(<SpendingPaceWidget scope={PAST} />)
    expect(await screen.findByText(t('widgets.spendingPace.noBudget'))).toBeInTheDocument()
  })
})

describe('SpendingDistributionWidget', () => {
  it('lists the categories that took the most, with their share', async () => {
    api.dashboard.spendingByCategory.mockResolvedValue([
      { category_id: 'rent', category_name: 'Rent', category_icon: 'house', category_color: '#111111', total: 700, projected_total: 750, percentage: 0 },
      { category_id: 'food', category_name: 'Food', category_icon: 'utensils', category_color: '#222222', total: 250, projected_total: 250, percentage: 0 },
      { category_id: 'none', category_name: 'Nothing', category_icon: 'x', category_color: '#333333', total: 0, projected_total: 0, percentage: 0 },
    ])
    renderWithProviders(<SpendingDistributionWidget scope={PAST} />)
    expect(await screen.findByText('Rent')).toBeInTheDocument()
    expect(screen.getByText('75%')).toBeInTheDocument()
    expect(screen.getByText(usd(1000))).toBeInTheDocument()
    expect(screen.queryByText('Nothing')).not.toBeInTheDocument()
  })

  it('loads nothing for a collection of wallets only, and says there is nothing', () => {
    renderWithProviders(<SpendingDistributionWidget scope={{ ...PAST, noAccounts: true }} />)
    expect(api.dashboard.spendingByCategory).not.toHaveBeenCalled()
    expect(screen.getByText(t('dashboard.noData'))).toBeInTheDocument()
  })
})

describe('MonthResultWidget', () => {
  it('shows income minus spending, and last month for comparison', async () => {
    api.dashboard.summary.mockImplementation(async (month: string) => (month === '2026-03-01' ? summary(2000, 1500) : summary(1000, 1200)))
    renderWithProviders(<MonthResultWidget scope={PAST} />)
    expect(await screen.findByText(`+${usd(500)}`)).toBeInTheDocument()
    expect(await screen.findByText(t('widgets.monthResult.previous', { amount: usd(-200) }))).toBeInTheDocument()
  })
})

describe('UpcomingWidget', () => {
  it('lists what is due soonest first', async () => {
    api.dashboard.projectedTransactions.mockResolvedValue([
      { recurring_id: 'b', description: 'Gym', amount: 50, amount_primary: 50, currency: 'USD', type: 'debit', date: '2026-04-20', category_icon: null, category_color: null },
      { recurring_id: 'a', description: 'Salary', amount: 3000, amount_primary: 3000, currency: 'USD', type: 'credit', date: '2026-04-05', category_icon: null, category_color: null },
    ])
    renderWithProviders(<UpcomingWidget scope={PAST} />)
    const salary = await screen.findByText('Salary')
    const gym = screen.getByText('Gym')
    expect(salary.compareDocumentPosition(gym) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText(`+${usd(3000)}`)).toBeInTheDocument()
  })

  it('keeps to the accounts in the active collection', async () => {
    api.dashboard.projectedTransactions.mockResolvedValue([
      { recurring_id: 'a', account_id: 'in', description: 'Rent', amount: 900, amount_primary: 900, currency: 'USD', type: 'debit', date: '2026-04-05', category_icon: null, category_color: null },
      { recurring_id: 'b', account_id: 'out', description: 'Gym', amount: 50, amount_primary: 50, currency: 'USD', type: 'debit', date: '2026-04-06', category_icon: null, category_color: null },
      { recurring_id: 'c', account_id: null, description: 'Cash', amount: 20, amount_primary: 20, currency: 'USD', type: 'debit', date: '2026-04-07', category_icon: null, category_color: null },
    ])
    renderWithProviders(<UpcomingWidget scope={{ ...PAST, activeAccountIds: ['in'] }} />)
    expect(await screen.findByText('Rent')).toBeInTheDocument()
    expect(screen.queryByText('Gym')).not.toBeInTheDocument()
    expect(screen.queryByText('Cash')).not.toBeInTheDocument()
  })

  it('says when nothing is due', async () => {
    api.dashboard.projectedTransactions.mockResolvedValue([])
    renderWithProviders(<UpcomingWidget scope={PAST} />)
    expect(await screen.findByText(t('widgets.upcoming.none', { days: 30 }))).toBeInTheDocument()
  })
})

describe('CreditCardsWidget', () => {
  it('adds up what the cards owe once, and what is left after paying', async () => {
    api.accounts.list.mockResolvedValue([
      account({ id: 'checking', type: 'checking', current_balance: 2000 }),
      account({ id: 'card-a', name: 'Card A', type: 'credit_card', current_balance: -300, shared_balance_group: 'line', next_due_date: '2026-04-20' }),
      account({ id: 'card-b', name: 'Card B', type: 'credit_card', current_balance: -300, shared_balance_group: 'line', next_due_date: '2026-04-10' }),
      account({ id: 'card-c', name: 'Card C', type: 'credit_card', current_balance: -200 }),
      account({ id: 'closed', name: 'Old', type: 'credit_card', current_balance: -999, is_closed: true }),
    ])
    renderWithProviders(<CreditCardsWidget scope={PAST} />)
    // A shared credit line counts once: 300 + 200.
    expect(await screen.findByText(usd(500))).toBeInTheDocument()
    expect(screen.getByText(t('widgets.cards.afterPaying', { amount: usd(1500) }))).toBeInTheDocument()
    expect(screen.queryByText('Old')).not.toBeInTheDocument()
    // The shared line is listed once, under both cards, so the rows add up to the total.
    expect(screen.getByText('Card A · Card B')).toBeInTheDocument()
    expect(screen.getAllByText(usd(300))).toHaveLength(1)
    // The line is due on the earlier of its cards' dates.
    const line = screen.getByText('Card A · Card B').closest('li')!
    expect(line).toHaveTextContent(new Date('2026-04-10T00:00:00').toLocaleDateString('en-US', { day: '2-digit', month: 'short' }))
  })

  it('says when there are no cards', async () => {
    api.accounts.list.mockResolvedValue([account({ type: 'checking', current_balance: 100 })])
    renderWithProviders(<CreditCardsWidget scope={PAST} />)
    expect(await screen.findByText(t('widgets.cards.none'))).toBeInTheDocument()
  })
})

describe('AssistantWidget', () => {
  function renderAssistant(available: boolean) {
    const ask = vi.fn()
    const result = renderWithProviders(
      <AssistantContext.Provider value={{ available, ask }}>
        <AssistantWidget />
      </AssistantContext.Provider>,
    )
    return { ask, ...result }
  }

  it('sends a typed question to the agent', async () => {
    const { ask, user } = renderAssistant(true)
    await user.type(screen.getByRole('textbox', { name: t('widgets.assistant.placeholder') }), 'How am I doing?')
    await user.click(screen.getByRole('button', { name: t('widgets.assistant.send') }))
    expect(ask).toHaveBeenCalledWith('How am I doing?')
    expect(screen.getByRole('textbox', { name: t('widgets.assistant.placeholder') })).toHaveValue('')
  })

  it('sends a suggestion with one click', async () => {
    const { ask, user } = renderAssistant(true)
    await user.click(screen.getByRole('button', { name: t('widgets.assistant.suggestion1') }))
    expect(ask).toHaveBeenCalledWith(t('widgets.assistant.suggestion1'))
  })

  it("explains itself when agents aren't available", () => {
    renderAssistant(false)
    expect(screen.getByText(t('widgets.assistant.unavailable'))).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })
})
