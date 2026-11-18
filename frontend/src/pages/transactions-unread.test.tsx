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
import { QueryClient } from '@tanstack/react-query'

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
const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('sonner', () => ({ toast }))
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
const workspace = vi.hoisted(() => ({ canWrite: true }))
vi.mock('@/contexts/workspace-context', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/contexts/workspace-context')>()
  return {
    ...actual,
    useWorkspace: () => ({
      canWrite: workspace.canWrite,
      hasModule: () => true,
      current: { id: 'ws-1' },
    }),
  }
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

/**
 * The list as it comes back while `items()` says whether rows were seen. The
 * review queue really narrows server-side, so an unread filter drops the rows
 * the test has already flipped to read.
 */
function listFromRows(items: () => Transaction[]) {
  api.transactions.list.mockImplementation(
    async (params?: { unread_only?: boolean }) => {
      const all = items()
      const rows = params?.unread_only ? all.filter((tx) => tx.is_read === false) : all
      return { items: rows, total: rows.length, page: 1, limit: 50 }
    },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
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
    let seen = false
    listFromRows(() => [row({ id: 'tx-new', description: 'MARKET PURCHASE', is_read: seen })])
    api.transactions.markRead.mockImplementation(async () => {
      seen = true
      return row({ id: 'tx-new', is_read: true })
    })

    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    await screen.findByTitle(NEW_TOOLTIP)
    fireEvent.click(await screen.findByText('MARKET PURCHASE'))

    await waitFor(() => expect(api.transactions.markRead).toHaveBeenCalledWith('tx-new', 'ws-1'))
    // The dialog opens and the badge is gone from the row that was refetched.
    await screen.findByRole('dialog')
    await waitFor(() => expect(screen.queryByTitle(NEW_TOOLTIP)).toBeNull())
  })

  it('keeps the row in the queue and says so when the write fails', async () => {
    listOnce([row({ id: 'tx-new', description: 'MARKET PURCHASE' })])
    api.transactions.markRead.mockRejectedValue(new Error('offline'))

    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    fireEvent.click(await screen.findByText('MARKET PURCHASE'))

    // Opening the row never waits on the write; the failure is only reported,
    // and the marker stays because nothing was persisted.
    await screen.findByRole('dialog')
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('An unexpected error occurred'),
    )
    expect(screen.getByTitle(NEW_TOOLTIP)).toBeTruthy()
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
    let cleared = false
    listFromRows(() =>
      cleared ? [] : [row({ id: 'tx-new', description: 'MARKET PURCHASE' })],
    )
    api.transactions.markAllRead.mockImplementation(async () => {
      cleared = true
      return { updated: 1 }
    })

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?unread_only=true',
      path: '/transactions',
    })

    // The bar renders before the list resolves, and its button stays disabled
    // until the queue has rows: wait for the count before clicking (issue #636).
    await screen.findByText('1 transaction to review')
    const button = await screen.findByRole('button', {
      name: 'Mark all as read in this workspace',
    })
    await waitFor(() => expect(button).not.toBeDisabled())
    fireEvent.click(button)

    await waitFor(() =>
      expect(api.transactions.markAllRead).toHaveBeenCalledWith('ws-1'),
    )
    // The queue really empties, not just the request going out.
    await waitFor(() => expect(screen.queryByText('MARKET PURCHASE')).toBeNull())
    await screen.findByText('0 transactions to review')
  })

  it('drops a selected row that leaves the review queue', async () => {
    let seen = false
    listFromRows(() => [
      row({ id: 'tx-open', description: 'OPEN ME', is_read: seen }),
      row({ id: 'tx-stay', description: 'STAY HERE' }),
    ])
    api.transactions.markRead.mockImplementation(async () => {
      seen = true
      return row({ id: 'tx-open', is_read: true })
    })

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?unread_only=true',
      path: '/transactions',
    })

    // Select the row through its checkbox, then open it: the refetch brings
    // the queue without it, and the selection must not keep a hidden id.
    await screen.findByText('2 transactions to review')
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    await screen.findByText('selected')

    fireEvent.click(screen.getByText('OPEN ME'))

    // The queue really lost the row (the dialog keeps its own copy of the
    // description), and the selection bar goes with it.
    await screen.findByText('1 transaction to review')
    await waitFor(() => expect(screen.queryByText('selected')).toBeNull())
  })
})

describe('paging through the list', () => {
  it('waits for a fresh answer before clamping the page back', async () => {
    // 20 rows per page. Page 2 was last cached when the queue held exactly 20
    // rows, so that response claims there is a single page; a sync then puts a
    // row back. Clicking Next must not bounce back to page 1 before the fresh
    // answer for page 2 arrives (issue #636).
    const firstPage = Array.from({ length: 20 }, (_, i) =>
      row({ id: `tx-${i + 1}`, description: `ROW ${i + 1}`, is_read: true }),
    )
    let pageTwoCalls = 0
    api.transactions.list.mockImplementation(async (params?: { page?: number }) => {
      if (params?.page === 2) {
        pageTwoCalls += 1
        return pageTwoCalls === 1
          ? { items: [], total: 20, page: 2, limit: 20 }
          : {
              items: [row({ id: 'tx-21', description: 'LATE ARRIVAL', is_read: true })],
              total: 21,
              page: 2,
              limit: 20,
            }
      }
      return { items: firstPage, total: 21, page: 1, limit: 20 }
    })

    // Keep a page around after it leaves the screen, the way the app does
    // (the test default throws every cached page away). The entry is stale, so
    // visiting it again serves it and refetches, which is what an invalidation
    // after a sync or an import leaves behind.
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: 0 } },
    })

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions',
      path: '/transactions',
      queryClient,
    })

    await screen.findByText('1 / 2')

    // The second page really is empty, so the clamp takes the person back.
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(pageTwoCalls).toBe(1))
    await screen.findByText('1 / 2')

    // Now page 2 is cached with the old total. Asking for it again serves that
    // stale answer while a fresh one is on the way; the page must not move.
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText('2 / 2')
    await screen.findByText('LATE ARRIVAL')
  })
})
