import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, screen, waitFor } from '@testing-library/react'
import TransactionsPage from '@/pages/transactions'
import { CollectionFilterProvider } from '@/contexts/collection-filter-provider'
import { useCollectionFilter } from '@/contexts/collection-filter-context'
import { api, WORKSPACE_STORAGE_KEY } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, Collection, TransactionCalendarResponse } from '@/types'

const scope = vi.hoisted(() => ({
  workspace: '10000000-0000-4000-8000-000000000001',
  collection: '10000000-0000-4000-8000-000000000002',
  flagged: '10000000-0000-4000-8000-000000000003',
  page: '10000000-0000-4000-8000-000000000004',
  outside: '10000000-0000-4000-8000-000000000005',
  wallet: '10000000-0000-4000-8000-000000000006',
  user: '10000000-0000-4000-8000-000000000007',
}))

// Keep the collection context/provider, calendar component and API methods real.
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: scope.user, preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/contexts/workspace-context')>(),
  useWorkspace: () => ({
    current: { id: scope.workspace, timezone: 'UTC' },
    canWrite: false, hasModule: () => false,
  }),
}))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US',
}))

function collection(accountIds: string[], walletIds: string[] = []): Collection {
  return {
    id: scope.collection, user_id: scope.user, name: 'Synthetic saved collection',
    icon: 'folder', color: '#336699', position: 0,
    account_ids: accountIds, account_count: accountIds.length,
    wallet_ids: walletIds, wallet_count: walletIds.length,
  }
}

function account(id: string, name: string, balance: number, excluded = false): Account {
  return {
    id, user_id: scope.user, connection_id: null, external_id: null,
    name, display_name: null, masked_number: null,
    institution_name: null, institution_logo_url: null, type: 'checking',
    balance, current_balance: balance, previous_balance: null, balance_primary: balance,
    currency: 'USD', balance_date: '2026-07-01', credit_limit: null,
    available_credit: null, statement_close_day: null, payment_due_day: null,
    next_close_date: null, next_due_date: null, minimum_payment: null,
    card_brand: null, card_level: null, shared_balance_group: null,
    is_closed: false, closed_at: null, exclude_from_reports: excluded,
  }
}

const flagged = account(scope.flagged, 'Flagged account', 300, true)
const pageAccount = account(scope.page, 'Page-selected account', 100)
const outside = account(scope.outside, 'Outside account', 9000)
const unfilteredLabel = 'Unfiltered calendar witness'
const flaggedLabel = 'Flagged calendar witness'
const pageLabel = 'Explicit page calendar witness'

function calendarResponse(selected: Account, description: string): TransactionCalendarResponse {
  return {
    month: '2026-07', currency: 'USD',
    account_ids: selected.id === scope.outside ? null : [selected.id],
    days: [{
      date: '2026-07-08', in_month: true, ending_balance: selected.balance,
      income: 0, expense: 30, transfer_net: 0,
      actual_income: 0, actual_expense: 30, actual_transfer_net: 0,
      projected_income: 0, projected_expense: 0, projected_transfer_net: 0,
      actual_count: 1, projected_count: 0,
      has_income: false, has_expense: true, has_transfer: false,
      items: [{
        kind: 'actual', id: '20000000-0000-4000-8000-000000000001', recurring_id: null,
        date: '2026-07-08', description, amount: 30, amount_primary: 30,
        currency: 'USD', type: 'debit', account_id: selected.id, account_name: selected.name,
        category_id: null, category_name: null, category_icon: null, category_color: null,
        status: 'posted', source: 'manual', transfer_pair_id: null,
        is_transfer: false, is_ignored: false, exclude_from_pnl: false,
      }],
    }],
  }
}

function deferredCollections() {
  let resolve!: (value: Collection[]) => void
  const promise = new Promise<Collection[]>((settle) => { resolve = settle })
  return { promise, resolve }
}

type Request = {
  method: string | undefined
  url: string | undefined
  baseURL: string | undefined
  workspace: unknown
  accountIds: unknown
}

const originalAdapter = api.defaults.adapter
let membership: ReturnType<typeof deferredCollections>
let requests: Request[]
let unexpected: Request[]

beforeEach(() => {
  membership = deferredCollections()
  requests = []
  unexpected = []
  localStorage.setItem(WORKSPACE_STORAGE_KEY, scope.workspace)
  localStorage.setItem(`securo.activeCollection.${scope.workspace}`, scope.collection)
  api.defaults.adapter = async (config) => {
    const accountIds: unknown = config.params?.account_ids
    const request = {
      method: config.method, url: config.url, baseURL: config.baseURL,
      workspace: config.headers['X-Workspace-Id'],
      accountIds: Array.isArray(accountIds) ? [...accountIds] : accountIds,
    }
    requests.push(request)
    let data: unknown
    if (config.method !== 'get') {
      unexpected.push(request)
      throw new Error(`Unexpected write in calendar fixture: ${config.method} ${config.url}`)
    }
    switch (config.url) {
      case '/collections':
        data = await membership.promise
        break
      case '/transactions/calendar':
        data = Array.isArray(accountIds) && accountIds[0] === scope.flagged
          ? calendarResponse(flagged, flaggedLabel)
          : Array.isArray(accountIds) && accountIds[0] === scope.page
            ? calendarResponse(pageAccount, pageLabel)
            : calendarResponse(outside, unfilteredLabel)
        break
      case '/transactions':
        data = { items: [], total: 0 }
        break
      case '/accounts':
        data = [flagged, pageAccount, outside]
        break
      case '/admin/accounting-mode':
        data = { mode: 'cash' }
        break
      case '/timezones':
        data = { default: 'UTC', available: ['UTC'] }
        break
      case '/categories':
      case '/category-groups':
      case '/payees':
      case '/recurring-transactions':
      case '/groups':
        data = []
        break
      default:
        unexpected.push(request)
        throw new Error(`Unhandled calendar fixture port: ${config.url}`)
    }
    // No assertions here: Axios/Query can isolate rejected adapter callbacks.
    return { data, status: 200, statusText: 'OK', headers: {}, config }
  }
})

afterEach(async () => {
  // Unmount first, then retire the delayed GET even if a test assertion failed.
  cleanup()
  try {
    await act(async () => {
      membership.resolve([])
      await membership.promise
    })
  } finally {
    api.defaults.adapter = originalAdapter
  }
})

function SelectionProbe() {
  const { activeCollectionId, activeAccountIds } = useCollectionFilter()
  return <>
    <output data-testid="saved-collection">{activeCollectionId}</output>
    <output data-testid="collection-accounts">{JSON.stringify(activeAccountIds)}</output>
  </>
}

const calendarRequests = () => requests.filter((request) => request.url === '/transactions/calendar')

async function mountCalendar(pageSelection = '') {
  const view = renderWithProviders(
    <CollectionFilterProvider><SelectionProbe /><TransactionsPage /></CollectionFilterProvider>,
    { route: `/transactions?view=calendar&from=2026-07-01&to=2026-07-31&day=2026-07-08${pageSelection}` },
  )
  // Mount/transport preconditions, not the regression oracle. The real calendar
  // mounts useEffectiveTimezone even when no financial data is available.
  await waitFor(() => expect(view.queryClient.getQueryState(['timezones'])?.status).toBe('success'))
  expect(screen.getAllByRole('button', { name: t('transactions.calendarView') })
    .some((button) => button.getAttribute('aria-pressed') === 'true')).toBe(true)
  expect(screen.getByTestId('saved-collection')).toHaveTextContent(scope.collection)
  expect(requests.filter((request) => request.url === '/collections')).toHaveLength(1)
  expect(view.queryClient.getQueryState(['collections'])?.fetchStatus).toBe('fetching')
  expect(view.queryClient.getQueryCache().getAll()
    .some((query) => query.queryKey[1] === 'calendar')).toBe(true)
  expect(unexpected).toEqual([])
  return view
}

async function resolveMembership(value: Collection) {
  await act(async () => {
    membership.resolve([value])
    await membership.promise
  })
  await waitFor(() => expect(screen.getByTestId('collection-accounts'))
    .toHaveTextContent(JSON.stringify(value.account_ids)))
}

it.each([
  { name: 'wallet-only', walletIds: [scope.wallet] },
  { name: 'entirely empty', walletIds: [] },
])('withholds calendar requests and data while a persisted $name collection loads', async ({ walletIds }) => {
  const view = await mountCalendar()
  const beforeRequests = [...calendarRequests()]
  const beforeData = view.queryClient.getQueriesData<TransactionCalendarResponse>({ queryKey: ['transactions', 'calendar'] })
    .map(([, data]) => data).filter((data) => data !== undefined)
  const beforeUnfilteredDisplay = screen.queryAllByText(unfilteredLabel).length

  await resolveMembership(collection([], walletIds))

  // Assert outside the adapter and after retiring the controlled transition.
  // Soft assertions preserve both unresolved-data and resolved-empty evidence.
  expect.soft(beforeRequests, 'no calendar transport while saved membership is unknown').toEqual([])
  expect.soft(beforeData, 'no implicit financial data cached during membership loading').toEqual([])
  expect.soft(beforeUnfilteredDisplay, 'no unfiltered calendar item displayed during loading').toBe(0)
  expect.soft(calendarRequests(), 'empty account membership must not start any calendar request').toEqual([])
  expect.soft(screen.queryAllByText(unfilteredLabel)).toHaveLength(0)
  expect.soft(view.queryClient.getQueryData(['transactions', 'calendar', '2026-07', []])).toBeUndefined()
  expect(unexpected).toEqual([])
})

it('requests exact flagged account IDs when persisted collection membership resolves', async () => {
  const view = await mountCalendar()
  const start = requests.length
  await resolveMembership(collection([scope.flagged]))
  await screen.findAllByText(flaggedLabel)
  const resolvedRequests = requests.slice(start).filter((request) => request.url === '/transactions/calendar')
  expect(resolvedRequests.length).toBeGreaterThan(0)
  for (const request of resolvedRequests) {
    expect(request.accountIds).toEqual([scope.flagged])
    expect(request.method).toBe('get')
    expect(request.baseURL).toBe('/api')
    expect(request.workspace).toBe(scope.workspace)
  }
  expect(view.queryClient.getQueryData<TransactionCalendarResponse>(
    ['transactions', 'calendar', '2026-07', [scope.flagged]],
  )).toMatchObject({ account_ids: [scope.flagged], days: [{ ending_balance: 300 }] })
  expect(unexpected).toEqual([])
})

it('uses explicit on-page account IDs while persisted collection membership is unresolved and after it resolves empty', async () => {
  const view = await mountCalendar(`&account_id=${scope.page}`)
  await screen.findAllByText(pageLabel)
  expect(view.queryClient.getQueryState(['collections'])?.fetchStatus).toBe('fetching')
  expect(calendarRequests().length).toBeGreaterThan(0)
  for (const request of calendarRequests()) {
    expect(request.accountIds).toEqual([scope.page])
    expect(request.method).toBe('get')
    expect(request.baseURL).toBe('/api')
    expect(request.workspace).toBe(scope.workspace)
  }
  expect(screen.queryAllByText(unfilteredLabel)).toHaveLength(0)

  await resolveMembership(collection([], [scope.wallet]))
  expect(screen.getAllByText(pageLabel).length).toBeGreaterThan(0)
  for (const request of calendarRequests()) expect(request.accountIds).toEqual([scope.page])
  expect(view.queryClient.getQueryData<TransactionCalendarResponse>(
    ['transactions', 'calendar', '2026-07', [scope.page]],
  )).toMatchObject({ account_ids: [scope.page], days: [{ ending_balance: 100 }] })
  expect(unexpected).toEqual([])
})
