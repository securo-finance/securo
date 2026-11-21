/**
 * The Balance column of a credit card (#920).
 *
 * It used to start at zero and add up only the selected cycle, while wearing
 * the same "Balance" heading as every other account. A card carrying a large
 * debt therefore showed a small positive number over a window that did not
 * begin at the account's start. Each row now carries the balance right after
 * it, seeded with the debt carried into the window, so the newest row lands
 * on the account's balance.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

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

const card = {
  id: 'acc-1',
  name: 'Card',
  type: 'credit_card',
  currency: 'BRL',
  balance: -850,
  is_active: true,
  credit_limit: 5000,
  statement_close_day: 30,
  payment_due_day: 10,
}

function tx(
  index: number,
  date: string,
  amount: number,
  type: 'debit' | 'credit',
  runningBalance: number | null,
): Transaction {
  return {
    id: `tx-${index}`,
    user_id: 'user-1',
    account_id: 'acc-1',
    category_id: null,
    category: null,
    external_id: null,
    description: `Payment ${index}`,
    original_description: null,
    amount,
    currency: 'BRL',
    date,
    type,
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
    reporting_date_override: null,
    splits: [],
    is_ignored: false,
    running_balance: runningBalance,
    running_balance_primary: null,
  }
}

// R$400 of debt came into the window (dated before it, so only the seed
// carries it), then the cycle charged 300 and 200 and was paid 50: -850.
// The cycle's own spend adds up to 450, which is what the old column showed.
const cycle = [
  tx(0, '2026-05-15', 50, 'credit', -850),
  tx(1, '2026-05-10', 200, 'debit', -900),
  tx(2, '2026-05-02', 300, 'debit', -700),
]

function balanceCell(description: string) {
  const row = screen.getByText(description).closest('tr')!
  return row.querySelectorAll('td')[4] as HTMLElement
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-09-15T12:00:00'))
  api.accounts.get.mockResolvedValue(card)
  api.accounts.bills.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([card])
  api.accounts.summary.mockResolvedValue({
    opening_balance: 0,
    current_balance: -850,
    monthly_income: 0,
    monthly_expenses: 0,
    projected_income: 0,
    projected_expenses: 0,
  })
  // The API only fills the balance when it is asked for it, and the stub does
  // the same: a page that stopped sending the flag would render an empty
  // column here, so what the tests below read is the request and its
  // consumer together, not a stub that answers either way.
  api.transactions.list.mockImplementation((params: { include_running_balance?: boolean }) =>
    Promise.resolve({
      items: params?.include_running_balance ? cycle : cycle.map((row) => ({ ...row, running_balance: null })),
      total: cycle.length,
    }),
  )
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.runningBalance.mockResolvedValue([])
})

describe("a credit card's balance column", () => {
  it('asks for the balance after each row of the statement', async () => {
    renderWithProviders(<AccountDetailPage />, {
      route: '/accounts/acc-1',
      path: '/accounts/:id',
    })

    await screen.findByText('Payment 0')
    expect(api.transactions.list).toHaveBeenCalledWith(
      expect.objectContaining({
        include_running_balance: true,
        statement_view: true,
      }),
    )
  })

  it("shows the balance carried into the window, not the cycle's own spend", async () => {
    renderWithProviders(<AccountDetailPage />, {
      route: '/accounts/acc-1',
      path: '/accounts/:id',
    })

    await screen.findByText('Payment 0')
    // The newest row reconciles with the account balance: -R$850.00, not the
    // R$450.00 the cycle's charges and the payment add up to.
    expect(balanceCell('Payment 0')).toHaveTextContent('-R$850.00')
    expect(balanceCell('Payment 1')).toHaveTextContent('-R$900.00')
    expect(balanceCell('Payment 2')).toHaveTextContent('-R$700.00')
    expect(screen.queryByText('R$450.00')).not.toBeInTheDocument()
  })

  it('styles the debt as a liability, like every other balance on the page', async () => {
    renderWithProviders(<AccountDetailPage />, {
      route: '/accounts/acc-1',
      path: '/accounts/:id',
    })

    await screen.findByText('Payment 0')
    expect(balanceCell('Payment 0').className).toContain('text-rose-500')
  })
})
