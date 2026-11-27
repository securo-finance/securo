/**
 * Paid or not (issue #15): an unpaid row carries the badge, clicking the badge
 * settles it without opening the dialog, and `?unpaid=1` narrows the list to
 * the rows still waiting to be paid.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { renderWithProviders } from '@/test/utils'
import type { Transaction } from '@/types'

const api = vi.hoisted(() => ({
  transactions: { list: vi.fn(), update: vi.fn(), delete: vi.fn(), create: vi.fn(), bulkFlag: vi.fn() },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: {
    get: vi.fn(), list: vi.fn(), bills: vi.fn(), summary: vi.fn(),
    update: vi.fn(), runningBalance: vi.fn(),
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
    collections: [], activeCollectionId: null, activeCollection: null,
    setActiveCollectionId: () => {}, activeAccountIds: null, activeWalletIds: null,
  }),
}))

function row(id: string, description: string, isPaid: boolean): Transaction {
  return {
    id, user_id: 'user-1', account_id: 'acc-1', description,
    category_id: null, category: null,
    external_id: null, original_description: null, amount: 42, currency: 'BRL',
    date: '2026-10-08', type: 'debit', source: 'manual', status: 'posted',
    payee: null, payee_id: null, payee_name: null, notes: null,
    transfer_pair_id: null, transfer_counterpart: null,
    amount_primary: null, fx_rate_used: null, fx_fallback: false,
    installment_number: null, total_installments: null, installment_total_amount: null,
    installment_purchase_date: null, installment_series_id: null,
    bill_id: null, effective_bill_date: null, reporting_date_override: null,
    splits: [], is_ignored: false, exclude_from_pnl: false, is_flagged: false,
    is_paid: isPaid,
  } as Transaction
}

const PENDING_ROW = row('tx-unpaid', 'Landlord', false)
const PAID_ROW = row('tx-paid', 'Supermarket', true)

// The server keeps the mark once the click lands, so a refetch of the list
// answers with the settled row.
let paidOnServer = false

beforeEach(() => {
  vi.clearAllMocks()
  paidOnServer = false
  vi.setSystemTime(new Date('2026-10-20T12:00:00'))
  // Answer like the server: under unpaid_only only the unpaid row comes back.
  api.transactions.list.mockImplementation(async (params: { unpaid_only?: boolean }) => {
    const items = [PENDING_ROW, PAID_ROW]
      .map((tx) => (tx.id === PENDING_ROW.id && paidOnServer ? { ...tx, is_paid: true } : tx))
      .filter((tx) => !params.unpaid_only || !tx.is_paid)
    return { items, total: items.length, page: 1, limit: 50 }
  })
  api.transactions.update.mockImplementation(
    async (id: string, payload: { is_paid?: boolean }) => {
      if (id === PENDING_ROW.id && payload.is_paid === true) paidOnServer = true
      return { ...PENDING_ROW, is_paid: true }
    },
  )
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

describe('paid transactions', () => {
  it('marks only the row still waiting to be paid', async () => {
    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    const unpaid = await screen.findByText('Landlord')
    const paid = await screen.findByText('Supermarket')

    expect(unpaid.parentElement!.querySelector('[aria-label="Mark as paid"]')).not.toBeNull()
    // A settled row says nothing: no badge, no second button per row.
    expect(paid.parentElement!.querySelector('[aria-label="Mark as paid"]')).toBeNull()
  })

  it('settles the row in one click, without opening the dialog', async () => {
    const { user } = renderWithProviders(<TransactionsPage />, {
      route: '/transactions',
      path: '/transactions',
    })

    const badge = await screen.findByRole('button', { name: 'Mark as paid' })
    await user.click(badge)

    await waitFor(() => {
      expect(api.transactions.update).toHaveBeenCalledWith('tx-unpaid', { is_paid: true })
    })
    // What the user sees: the badge is gone once the list comes back.
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Mark as paid' })).not.toBeInTheDocument()
    })
    // No dialog: the edit sheet would have asked for a save.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('offers the unpaid filter inside the filter menu too, for a phone', async () => {
    const { user } = renderWithProviders(<TransactionsPage />, {
      route: '/transactions',
      path: '/transactions',
    })
    await screen.findByText('Supermarket')

    await user.click(screen.getByRole('button', { name: 'Filters' }))
    // The menu opens on its root; "Only unpaid" is one of its groups. The
    // desktop group of the same menu is in the document too (it is only hidden
    // by CSS), so the phone control is the first of a pair.
    const unpaidItems = screen.getAllByRole('menuitem', { name: 'Only unpaid' })
    expect(unpaidItems).toHaveLength(2)
    await user.click(unpaidItems[0])
    // And the group itself offers the two states. The desktop group of the
    // same menu is in the document too (it is only hidden by CSS), so the
    // mobile control is the first of each pair.
    await user.click(screen.getAllByRole('menuitem', { name: 'Only unpaid' })[0])

    // What the user sees: the list comes back with only the row still unpaid.
    await waitFor(() => {
      expect(screen.queryByText('Supermarket')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Landlord')).toBeInTheDocument()
  })

  it('asks the server for only the unpaid rows when the URL says so', async () => {
    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?unpaid=1',
      path: '/transactions',
    })

    expect(await screen.findByText('Landlord')).toBeInTheDocument()
    expect(screen.queryByText('Supermarket')).not.toBeInTheDocument()
    expect(api.transactions.list.mock.calls.some(([params]) => params.unpaid_only === true)).toBe(true)
  })
})
