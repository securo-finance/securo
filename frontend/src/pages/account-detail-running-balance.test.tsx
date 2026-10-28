/**
 * Account balances past the first page of transactions (#1202).
 *
 * The page used to fetch one page of 500 rows and walk them from the opening
 * balance, so every row past the 500th was never applied: the Balance column,
 * the chart and the Projected Balance card all stopped short of the truth.
 * A non-credit-card account now pages through the list and takes each row's
 * balance, and the chart's series, from the server. A credit card keeps its
 * cycle math, over every page of the cycle.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

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

const card = {
  id: 'acc-1', name: 'Card', type: 'credit_card', currency: 'BRL',
  balance: -501, is_active: true, credit_limit: 5000,
  statement_close_day: 30, payment_due_day: 10,
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

// 150 rows newest first, six a day from 2026-09-27 down; the server's
// balance after row i is 9000 - 10i.
const ledger = Array.from({ length: 150 }, (_, i) =>
  tx(i, `2026-09-${String(27 - Math.floor(i / 6)).padStart(2, '0')}`, 9000 - 10 * i),
)

function listPage(rows: Transaction[], args: { page?: number; limit?: number }) {
  const limit = args.limit ?? 50
  const page = args.page ?? 1
  return { items: rows.slice((page - 1) * limit, page * limit), total: rows.length, page, limit }
}

function balanceCell(description: string) {
  const row = screen.getByText(description).closest('tr')!
  return row.querySelectorAll('td')[4]
}

async function renderPage() {
  return renderWithProviders(<AccountDetailPage />, {
    route: '/accounts/acc-1',
    path: '/accounts/:id',
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-09-15T12:00:00'))
  api.accounts.list.mockResolvedValue([])
  api.accounts.bills.mockResolvedValue([])
  api.accounts.summary.mockResolvedValue({
    opening_balance: 0, current_balance: 9000, monthly_income: 0, monthly_expenses: 0,
    projected_income: 0, projected_expenses: 0,
  })
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.accounts.runningBalance.mockResolvedValue([])
})

describe('a non-credit-card account with more rows than one page', () => {
  beforeEach(() => {
    api.accounts.get.mockResolvedValue(checking)
    api.transactions.list.mockImplementation(async (args) => listPage(ledger, args))
    api.dashboard.projectedTransactions.mockResolvedValue([{
      recurring_id: 'rec-1', account_id: 'acc-1', description: 'Rent', amount: 500,
      amount_primary: null, currency: 'BRL', type: 'debit', date: '2026-09-28',
      category_id: null, category_name: null, category_icon: null, category_color: null,
      category_treat_as_transfer: false,
    }])
  })

  it('shows the balance the server computed over the whole period', async () => {
    await renderPage()

    expect(await screen.findByText('Payment 0')).toBeVisible()
    expect(balanceCell('Payment 0')).toHaveTextContent('R$9,000.00')
    expect(balanceCell('Payment 99')).toHaveTextContent('R$8,010.00')
    // The projection sits on top of the newest row it follows.
    expect(balanceCell('Rent')).toHaveTextContent('R$8,500.00')
    expect(screen.queryByText('Payment 100')).not.toBeInTheDocument()
    expect(api.transactions.list).toHaveBeenCalledWith(expect.objectContaining({
      page: 1, limit: 100, include_running_balance: true, include_opening_balance: true,
    }))
  })

  it('takes the Projected Balance card from the newest row', async () => {
    await renderPage()

    const card = (await screen.findByText('Projected Balance')).parentElement!
    await waitFor(() => expect(within(card).getByText('R$8,500.00')).toBeVisible())
    expect(within(card).getByText('Not settled: R$500.00')).toBeVisible()
  })

  it('appends the next page when asked to show more', async () => {
    const { user } = await renderPage()

    await user.click(await screen.findByRole('button', { name: '+50 more' }))

    expect(await screen.findByText('Payment 149')).toBeVisible()
    expect(balanceCell('Payment 149')).toHaveTextContent('R$7,510.00')
    expect(balanceCell('Payment 0')).toHaveTextContent('R$9,000.00')
    expect(api.transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, limit: 100 }))
    expect(screen.queryByRole('button', { name: /more$/ })).not.toBeInTheDocument()
  })

  it('draws the chart from the running-balance series', async () => {
    // One point a day; nothing in the loaded rows comes close to 20,000.
    api.accounts.runningBalance.mockResolvedValue(
      Array.from({ length: 30 }, (_, i) => ({
        date: `2026-09-${String(i + 1).padStart(2, '0')}`,
        balance: 20000,
        balance_primary: null,
      })),
    )

    await renderPage()

    expect(await screen.findByText('R$20,000')).toBeInTheDocument()
    expect(api.accounts.runningBalance).toHaveBeenCalledWith('acc-1', '2026-09-01', '2026-09-30')
  })
})

describe('a credit-card cycle with more rows than one page', () => {
  it('loads every page of the cycle', async () => {
    api.accounts.get.mockResolvedValue(card)
    // One charge past a full page, newest first, 25 a day from 2026-09-21.
    const cycle = Array.from({ length: 501 }, (_, i) =>
      tx(i, `2026-09-${String(21 - Math.floor(i / 25)).padStart(2, '0')}`, null),
    )
    api.transactions.list.mockImplementation(async (args) => listPage(cycle, args))

    await renderPage()

    expect(await screen.findByText('Payment 500')).toBeInTheDocument()
    // The top row carries the cumulative cycle spend: all 501 charges, not
    // the first 500.
    const topRow = screen.getAllByRole('row')[1]
    expect(topRow.querySelectorAll('td')[4]).toHaveTextContent('R$501.00')
    expect(api.transactions.list).toHaveBeenCalledWith(expect.objectContaining({ page: 2, limit: 500 }))
    expect(api.accounts.runningBalance).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /more$/ })).not.toBeInTheDocument()
    // Over 500 table rows is the point of the test, and rendering them under
    // a fully parallel run takes longer than the default 5s.
  }, 20_000)
})
