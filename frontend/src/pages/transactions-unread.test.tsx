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
  fiscal: { taxIdKinds: vi.fn(), jurisdictions: vi.fn(), productFields: vi.fn() },
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
  fiscal: api.fiscal,
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
    async (params?: { unread_only?: boolean; from?: string; to?: string }) => {
      const rows = items().filter((tx) =>
        (!params?.unread_only || tx.is_read === false)
        && (!params?.from || tx.date >= params.from)
        && (!params?.to || tx.date <= params.to))
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
  api.fiscal.taxIdKinds.mockResolvedValue([])
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

  it('does not badge a row shared from another workspace as new', async () => {
    // Opening a shared row never clears its marker (it belongs to another
    // workspace), so a badge here could never go away.
    listOnce([
      row({ id: 'tx-shared', description: 'PAID FOR THE GROUP', is_shared: true }),
    ])

    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    await screen.findByText('PAID FOR THE GROUP')
    expect(screen.queryByTitle(NEW_TOOLTIP)).toBeNull()
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

  it('opens the queue on every date, not on the current month (issue #411)', async () => {
    // An import early in November brings October's rows: the queue must show
    // them, and its count must be the one the API gives without dates.
    vi.setSystemTime(new Date('2026-11-05T12:00:00'))
    listFromRows(() => [row({ id: 'tx-oct', description: 'OCTOBER IMPORT', date: '2026-10-08' })])

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?unread_only=true',
      path: '/transactions',
    })

    await screen.findByText('OCTOBER IMPORT')
    await screen.findByText('1 transaction to review')
    const listCall = api.transactions.list.mock.calls.at(-1)![0]
    expect(listCall.from).toBeUndefined()
    expect(listCall.to).toBeUndefined()
  })

  it('keeps the queue chip when it is the only filter left (issue #411)', async () => {
    // The queue drops the date, so its chip is the only one on the row: the
    // row must still show, and the chip's x must leave the queue.
    vi.setSystemTime(new Date('2026-11-05T12:00:00'))
    listFromRows(() => [row({ id: 'tx-oct', description: 'OCTOBER IMPORT', date: '2026-10-08' })])

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?unread_only=true',
      path: '/transactions',
    })

    await screen.findByText('OCTOBER IMPORT')
    fireEvent.click(await screen.findByRole('button', { name: /New transactions:\s*New only/ }))

    await waitFor(() => {
      const call = api.transactions.list.mock.calls.at(-1)![0]
      expect(call.unread_only).toBeUndefined()
      expect(call.from).toBe('2026-11-01')
    })
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
    // until the list has loaded: wait for the count before clicking (issue #636).
    await screen.findByText('1 transaction to review')
    const button = await screen.findByRole('button', {
      name: 'Mark all as read in this workspace (1)',
    })
    await waitFor(() => expect(button).not.toBeDisabled())
    fireEvent.click(button)

    await waitFor(() =>
      expect(api.transactions.markAllRead).toHaveBeenCalledWith('ws-1'),
    )
    // The queue really empties, not just the request going out.
    await waitFor(() => expect(screen.queryByText('MARKET PURCHASE')).toBeNull())
    await screen.findByText('Nothing left to review')
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Mark all as read in this workspace (0)' }))
        .toBeDisabled(),
    )
  })

  it('drops the month when the queue is turned on and puts it back after', async () => {
    listFromRows(() => [row({ id: 'tx-sep', description: 'SEPTEMBER ROW', date: '2026-09-12' })])

    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    await waitFor(() =>
      expect(api.transactions.list.mock.calls.at(-1)![0].from).toBe('2026-10-01'),
    )
    const trigger = screen.getByRole('button', { name: 'Filters' })
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: 'mouse' })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Only new transactions' }))

    await screen.findByText('SEPTEMBER ROW')
    const queueCall = api.transactions.list.mock.calls.at(-1)![0]
    expect(queueCall.unread_only).toBe(true)
    expect(queueCall.from).toBeUndefined()

    // The item keeps the menu open, so the same click turns the queue off.
    fireEvent.click(screen.getByRole('menuitem', { name: 'Only new transactions' }))

    await waitFor(() => {
      const call = api.transactions.list.mock.calls.at(-1)![0]
      expect(call.unread_only).toBeUndefined()
      expect(call.from).toBe('2026-10-01')
      expect(call.to).toBe('2026-10-31')
    })
  })

  it('says how many rows outside the filters mark all would clear (issue #411)', async () => {
    // The action clears the whole workspace and ignores the list's filters: a
    // range with nothing left to review keeps it on, and both the banner and
    // the button say how many rows it reaches.
    listFromRows(() => [row({ id: 'tx-sep', description: 'SEPTEMBER ROW', date: '2026-09-12' })])

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?unread_only=true&from=2026-10-01&to=2026-10-31',
      path: '/transactions',
    })

    await screen.findByText('Nothing left to review')
    await screen.findByText('Outside these filters: 1')
    const button = await screen.findByRole('button', {
      name: 'Mark all as read in this workspace (1)',
    })
    await waitFor(() => expect(button).not.toBeDisabled())
    fireEvent.click(button)

    await waitFor(() => expect(api.transactions.markAllRead).toHaveBeenCalledWith('ws-1'))
  })

  it('turns mark all off when the workspace has nothing unread', async () => {
    listFromRows(() => [])

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?unread_only=true',
      path: '/transactions',
    })

    await screen.findByText('Nothing left to review')
    const button = await screen.findByRole('button', {
      name: 'Mark all as read in this workspace (0)',
    })
    expect(button).toBeDisabled()
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
  const firstPage = Array.from({ length: 20 }, (_, i) =>
    row({ id: `tx-${i + 1}`, description: `ROW ${i + 1}`, is_read: true }),
  )

  /**
   * 20 rows per page. Page 2 is first answered when the queue held exactly 20
   * rows, so that response claims there is a single page; a sync then puts a
   * row back, and `second` is what page 2 says the next time it is asked.
   */
  function pagedList(second: () => Promise<unknown>) {
    const calls = { pageTwo: 0 }
    api.transactions.list.mockImplementation(async (params?: { page?: number }) => {
      if (params?.page === 2) {
        calls.pageTwo += 1
        return calls.pageTwo === 1
          ? { items: [], total: 20, page: 2, limit: 20 }
          : second()
      }
      return { items: firstPage, total: 21, page: 1, limit: 20 }
    })
    return calls
  }

  /**
   * The list is on screen and the search box's mount debounce, which sends the
   * page back to 1 after 300 ms, has already fired, so it cannot be mistaken
   * for a clamp.
   */
  async function firstPageSettled() {
    await screen.findByText('1 / 2')
    await new Promise((resolve) => setTimeout(resolve, 350))
  }

  const lateArrival = async () => ({
    items: [row({ id: 'tx-21', description: 'LATE ARRIVAL', is_read: true })],
    total: 21,
    page: 2,
    limit: 20,
  })

  // Keep a page around after it leaves the screen, the way the app does (the
  // test default throws every cached page away). A stale entry is served and
  // refetched on the next visit, which is what an invalidation after a sync or
  // an import leaves behind; a fresh one is the app's own five minutes.
  it.each([
    { cache: 'stale', staleTime: 0 },
    { cache: 'fresh', staleTime: 1000 * 60 * 5 },
  ])('asks a $cache cached page again before clamping it back', async ({ staleTime }) => {
    // Clicking Next must not bounce back to page 1 on the cached total: the
    // page is asked again, and the fresh answer for it decides (issue #636).
    const calls = pagedList(lateArrival)
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime } },
    })

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions',
      path: '/transactions',
      queryClient,
    })

    await firstPageSettled()

    // The second page really is empty, so the clamp takes the person back.
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(calls.pageTwo).toBe(1))
    await screen.findByText('1 / 2')

    // Now page 2 is cached with the old total. Asking for it again must reach
    // the server before the page moves.
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText('2 / 2')
    await screen.findByText('LATE ARRIVAL')
    expect(calls.pageTwo).toBe(2)
  })

  it('stays on the page when its refetch fails', async () => {
    // A failed answer is not a new total: the cached one stays on screen and
    // the person is left on the page they asked for, so Refresh can retry it.
    const calls = pagedList(async () => {
      throw new Error('offline')
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: 0 } },
    })

    renderWithProviders(<TransactionsPage />, {
      route: '/transactions',
      path: '/transactions',
      queryClient,
    })

    await firstPageSettled()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(calls.pageTwo).toBe(1))
    await screen.findByText('1 / 2')

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(calls.pageTwo).toBe(2))
    await waitFor(() =>
      expect(queryClient.getQueryCache().findAll({ queryKey: ['transactions', 2] })[0]
        .state.status).toBe('error'),
    )

    // Page 1 is cached, so a clamp would put its rows and pager back at once.
    expect(screen.queryByText('1 / 2')).toBeNull()
    expect(screen.queryByText('ROW 1')).toBeNull()
    expect(api.transactions.list.mock.calls.at(-1)![0].page).toBe(2)
  })
})
