/**
 * Projected rows in a transfer category (#993).
 *
 * A recurring transaction whose category is flagged treat_as_transfer shows
 * up in Account Detail as a projected row. That row must carry the flag so
 * it gets the same Transfer badge as a posted row in that category.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import AccountDetailPage from '@/pages/account-detail'
import { renderWithProviders } from '@/test/utils'
import type { Transaction } from '@/types'

const api = vi.hoisted(() => ({
  accounts: {
    get: vi.fn(),
    bills: vi.fn(),
    summary: vi.fn(),
    list: vi.fn(),
    update: vi.fn(),
    runningBalance: vi.fn(),
  },
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

const checking = {
  id: 'acc-1', name: 'Checking', type: 'checking', currency: 'BRL',
  balance: 9000, current_balance: 9000, is_active: true,
}

/** A R$1 debit. Its amount alone can never add up to the balances below,
 *  so any balance on screen that matches them came from the server. */
function tx(index: number, date: string, runningBalance: number | null): Transaction {
  return {
    id: `tx-${index}`, user_id: 'user-1', account_id: 'acc-1', category_id: null,
    category: null, external_id: null, description: `Payment ${index}`,
    original_description: null, amount: 1, currency: 'BRL', date, type: 'debit',
    source: 'manual', status: 'posted', payee: null, payee_id: null,
    payee_name: null, notes: null, transfer_pair_id: null, amount_primary: null,
    fx_rate_used: null, fx_fallback: false, installment_number: null,
    total_installments: null, installment_total_amount: null,
    installment_purchase_date: null, installment_series_id: null, bill_id: null,
    effective_bill_date: null, splits: [], is_ignored: false,
    running_balance: runningBalance, running_balance_primary: null,
  }
}

function projection(description: string, treatAsTransfer: boolean) {
  return {
    recurring_id: `rec-${description}`, account_id: 'acc-1', description, amount: 500,
    amount_primary: null, currency: 'BRL', type: 'debit', date: '2026-09-28',
    category_id: 'cat-1', category_name: 'Savings', category_icon: 'piggy-bank',
    category_color: '#000000', category_treat_as_transfer: treatAsTransfer,
  }
}

function row(description: string) {
  return screen.getByText(description).closest('tr')!
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-09-15T12:00:00'))
  api.accounts.get.mockResolvedValue(checking)
  api.accounts.list.mockResolvedValue([])
  api.accounts.bills.mockResolvedValue([])
  api.accounts.summary.mockResolvedValue({
    opening_balance: 0, current_balance: 9000, monthly_income: 0, monthly_expenses: 0,
    projected_income: 0, projected_expenses: 0,
  })
  api.accounts.runningBalance.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.transactions.list.mockResolvedValue({ items: [tx(0, '2026-09-10', 9000)], total: 1, page: 1, limit: 100 })
})

describe('a projected row', () => {
  it('shows the Transfer badge when its category is a transfer category', async () => {
    api.dashboard.projectedTransactions.mockResolvedValue([projection('Move to savings', true)])

    renderWithProviders(<AccountDetailPage />, { route: '/accounts/acc-1', path: '/accounts/:id' })

    expect(await screen.findByText('Move to savings')).toBeVisible()
    expect(within(row('Move to savings')).getByText('Transfer')).toBeVisible()
  })

  it('shows no Transfer badge for a normal category', async () => {
    api.dashboard.projectedTransactions.mockResolvedValue([projection('Rent', false)])

    renderWithProviders(<AccountDetailPage />, { route: '/accounts/acc-1', path: '/accounts/:id' })

    expect(await screen.findByText('Rent')).toBeVisible()
    expect(within(row('Rent')).queryByText('Transfer')).not.toBeInTheDocument()
  })
})
