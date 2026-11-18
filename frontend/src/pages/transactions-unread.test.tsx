/**
 * Review state on the transactions list (issue #636).
 *
 * A row that arrived from sync or an import is unread until the person opens
 * it: the list badges it as new, "only new transactions" narrows the list to
 * the review queue, opening a row clears its marker, and "mark all as read"
 * clears the rest in one call.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { renderWithProviders } from '@/test/utils'
import type { Transaction } from '@/types'

const api = vi.hoisted(() => ({
  transactions: {
    list: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    create: vi.fn(),
    markRead: vi.fn(),
    markAllRead: vi.fn(),
  },
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

const NEW_TOOLTIP = 'Arrived by sync or import and not opened yet'

function row(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: 'tx-1', user_id: 'user-1', account_id: 'acc-1', category_id: null,
    category: null, external_id: 'bank-1', original_description: null,
    description: 'MARKET PURCHASE', amount: 25, currency: 'BRL',
    date: '2026-10-08', type: 'debit', source: 'sync', status: 'posted',
    payee: null, payee_id: null, payee_name: null, notes: null,
    transfer_pair_id: null, amount_primary: null, fx_rate_used: null,
    fx_fallback: false, installment_number: null, total_installments: null,
    installment_total_amount: null, installment_purchase_date: null,
    installment_series_id: null, bill_id: null, effective_bill_date: null,
    reporting_date_override: null, splits: [], is_ignored: false,
    is_read: false,
    ...overrides,
  }
}

function listOnce(items: Transaction[]) {
  api.transactions.list.mockResolvedValue({ items, total: items.length, page: 1, limit: 50 })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-10-20T12:00:00'))
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
  api.transactions.markRead.mockResolvedValue(row({ is_read: true }))
  api.transactions.markAllRead.mockResolvedValue({ updated: 1 })
})

describe('review state of a transaction row', () => {
  it('marks an unread row as new and leaves a read one alone', async () => {
    listOnce([
      row({ id: 'tx-new', description: 'MARKET PURCHASE' }),
      row({ id: 'tx-seen', description: 'OLD COFFEE', is_read: true }),
    ])

    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    const seen = await screen.findByText('OLD COFFEE')
    const fresh = await screen.findByText('MARKET PURCHASE')

    const badgeOf = (description: HTMLElement) =>
      Array.from(description.parentElement!.querySelectorAll('[title]'))
        .find((el) => el.getAttribute('title') === NEW_TOOLTIP)

    expect(badgeOf(fresh)).toBeTruthy()
    expect(badgeOf(seen)).toBeUndefined()
  })

  it('opening an unread row clears its marker', async () => {
    listOnce([row({ id: 'tx-new', description: 'MARKET PURCHASE' })])

    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    fireEvent.click(await screen.findByText('MARKET PURCHASE'))

    await waitFor(() => expect(api.transactions.markRead).toHaveBeenCalledWith('tx-new'))
  })

  it('marks an already read row without calling the server', async () => {
    listOnce([row({ id: 'tx-seen', description: 'OLD COFFEE', is_read: true })])

    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    fireEvent.click(await screen.findByText('OLD COFFEE'))

    // The row opens the edit dialog either way; only an unread row also talks
    // to the server, and this one must stay silent (issue #636).
    await screen.findByRole('dialog')
    expect(api.transactions.markRead).not.toHaveBeenCalled()
  })

  it('shows only the review queue when the filter is on', async () => {
    listOnce([row({ id: 'tx-new', description: 'MARKET PURCHASE' })])

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?unread_only=true',
      path: '/transactions',
    })

    await screen.findByText('1 transaction to review')
    const listCall = api.transactions.list.mock.calls.at(-1)![0]
    expect(listCall.unread_only).toBe(true)
  })

  it('marks every row as read from the queue', async () => {
    listOnce([row({ id: 'tx-new', description: 'MARKET PURCHASE' })])

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?unread_only=true',
      path: '/transactions',
    })

    // The bar renders before the list resolves, and its button stays disabled
    // until the queue has rows: wait for the count before clicking (issue #636).
    await screen.findByText('1 transaction to review')
    const button = await screen.findByRole('button', { name: 'Mark all as read' })
    await waitFor(() => expect(button).not.toBeDisabled())
    fireEvent.click(button)

    await waitFor(() => expect(api.transactions.markAllRead).toHaveBeenCalledTimes(1))
  })
})
