/**
 * The Date column shows the date the list orders and files a row by (#1386).
 *
 * In accrual mode a card purchase is filed on its bill's due date, so a
 * March 30 purchase sits on April's page above an April 5 row. The column
 * showed the purchase date, and the order looked wrong. The list API now
 * sends `reporting_date`; the column shows it, and the row keeps the bank
 * date beside it.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { ReportingDateBadge } from '@/components/reporting-date-badge'
import { listDate } from '@/lib/list-date'
import { renderWithProviders } from '@/test/utils'
import type { Transaction } from '@/types'

const api = vi.hoisted(() => ({
  transactions: { list: vi.fn(), update: vi.fn(), delete: vi.fn(), create: vi.fn() },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: {
    get: vi.fn(),
    list: vi.fn(),
    bills: vi.fn(),
    summary: vi.fn(),
    update: vi.fn(),
    runningBalance: vi.fn(),
  },
  recurring: { list: vi.fn() },
  payees: { list: vi.fn() },
  admin: { accountingMode: vi.fn() },
  groups: { get: vi.fn(), list: vi.fn() },
  rules: { create: vi.fn() },
  reconciliation: { suggestions: vi.fn() },
  currencies: { list: vi.fn() },
  settings: { attachments: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  transactions: api.transactions,
  categories: api.categories,
  categoryGroups: api.categoryGroups,
  accounts: api.accounts,
  recurring: api.recurring,
  payees: api.payees,
  admin: api.admin,
  groups: api.groups,
  rules: api.rules,
  reconciliation: api.reconciliation,
  currencies: api.currencies,
  settings: api.settings,
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
  useAuth: () => ({ user: { id: 'user-1', preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/contexts/workspace-context', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/contexts/workspace-context')>()
  return { ...actual, useWorkspace: () => ({ canWrite: true, hasModule: () => true }) }
})
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({
    collections: [],
    activeCollectionId: null,
    activeCollection: null,
    setActiveCollectionId: () => {},
    activeAccountIds: null,
    activeWalletIds: null,
  }),
}))

function tx(over: Partial<Transaction> & { id: string; description: string }): Transaction {
  return {
    user_id: 'user-1',
    account_id: 'acc-1',
    category_id: null,
    category: null,
    external_id: null,
    original_description: null,
    amount: 1,
    currency: 'BRL',
    date: '2026-04-05',
    type: 'debit',
    source: 'manual',
    status: 'posted',
    payee: null,
    payee_id: null,
    payee_name: null,
    notes: null,
    transfer_pair_id: null,
    transfer_counterpart: null,
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
    ...over,
    reporting_date_override: over.reporting_date_override ?? null,
  }
}

// What the list API returns for April in accrual mode, in its order.
const rows: Transaction[] = [
  tx({ id: 'tx-card', description: 'March purchase', date: '2026-03-30', reporting_date: '2026-04-10' }),
  tx({ id: 'tx-rent', description: 'April rent', date: '2026-04-05', reporting_date: '2026-04-05' }),
]

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-04-20T12:00:00'))
  api.transactions.list.mockResolvedValue({ items: rows, total: rows.length, page: 1, limit: 50 })
  api.categories.list.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([])
  api.recurring.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
  api.admin.accountingMode.mockResolvedValue(false)
  api.groups.list.mockResolvedValue([])
  api.reconciliation.suggestions.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.settings.attachments.mockResolvedValue(false)
})

describe('the Date column of the transactions list', () => {
  it('shows the date each row is ordered by, top to bottom', async () => {
    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    const purchase = (await screen.findByText('March purchase')).closest('tr')!
    const rent = screen.getByText('April rent').closest('tr')!
    const dateCell = (row: HTMLElement) => within(row).getAllByRole('cell')[1]
    expect(dateCell(purchase)).toHaveTextContent('4/10/2026')
    expect(dateCell(rent)).toHaveTextContent('4/5/2026')
    // The bank date stays on the row that is filed elsewhere.
    expect(purchase).toHaveTextContent('Bank date: 3/30/2026')
    expect(rent).not.toHaveTextContent('Bank date')
  })
})

describe('listDate', () => {
  it('prefers the server date, then the override, then the bank date', () => {
    const base = { date: '2026-03-30', reporting_date_override: null }
    expect(listDate({ ...base, reporting_date: '2026-04-10' })).toBe('2026-04-10')
    expect(listDate({ ...base, reporting_date_override: '2026-04-01' })).toBe('2026-04-01')
    expect(listDate(base)).toBe('2026-03-30')
  })

  it('the badge stays hidden when the row is filed on its bank date', () => {
    const { container } = renderWithProviders(
      <ReportingDateBadge
        tx={{ date: '2026-04-05', reporting_date_override: null, reporting_date: '2026-04-05' }}
        locale="en-US"
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('the badge keeps the override on a card statement row filed by its bank date', () => {
    // The statement view sends `reporting_date` as the bill/bank date and
    // leaves the override out of it.
    renderWithProviders(
      <ReportingDateBadge
        tx={{ date: '2026-03-30', reporting_date_override: '2026-04-15', reporting_date: '2026-03-30' }}
        locale="en-US"
      />,
    )
    expect(
      screen.getByText(new RegExp(new Date('2026-04-15T00:00:00').toLocaleDateString('en-US'))),
    ).toBeInTheDocument()
  })

  it('the badge keeps the bank date readable when the column shows another date', () => {
    // Filed on its bill date while the override pins the bank date: the
    // Date column shows 4/10, so 3/30 has to stay visible somewhere.
    renderWithProviders(
      <ReportingDateBadge
        tx={{ date: '2026-03-30', reporting_date_override: '2026-03-30', reporting_date: '2026-04-10' }}
        locale="en-US"
      />,
    )
    expect(screen.getByText('Bank date: 3/30/2026')).toBeInTheDocument()
    expect(screen.queryByText(/Reporting date/)).not.toBeInTheDocument()
  })
})
