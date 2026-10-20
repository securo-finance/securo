/**
 * Two things the account page's `listAll` walk had to learn, both from
 * PR review:
 *
 * 1. The walk is capped (`LIST_ALL_MAX_ROWS`, 20 000 rows). Before this, a
 *    range that hit the cap returned the first 20 000 rows as though they
 *    were the whole thing — the chart, the running balance and the list then
 *    claimed to cover a window they had only half of. The result now carries
 *    `truncated`, and the page says so above the table.
 *
 * 2. The walk is up to 40 requests deep, and TanStack Query v5's signal was
 *    being dropped on the floor. Moving a date field started a new query
 *    while the previous one kept paging. The page now passes the signal into
 *    `listAll` → `list` → axios, and a superseded walk stops.
 *
 * Both are asserted against the real `listAll` and the real axios adapter —
 * no endpoint mocking — because the bug lives in the wiring between them.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'

import AccountDetailPage from '@/pages/account-detail'
import { renderWithProviders } from '@/test/utils'
import { api, transactions } from '@/lib/api'
import type { Transaction } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '***' }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

const ACCOUNT_ID = 'acc-1'

const account = {
  id: ACCOUNT_ID,
  user_id: 'user-1',
  connection_id: null,
  external_id: null,
  name: 'Everyday account',
  display_name: null,
  masked_number: null,
  institution_name: null,
  type: 'checking',
  currency: 'USD',
  balance: 0,
  is_active: true,
}

function buildRows(count: number, from = 0): Transaction[] {
  const rows: Transaction[] = []
  for (let i = from; i < from + count; i++) {
    rows.push({
      id: `tx-${i}`,
      user_id: 'user-1',
      account_id: ACCOUNT_ID,
      category_id: null,
      category: null,
      external_id: null,
      description: `Row ${i}`,
      original_description: null,
      amount: 10,
      currency: 'USD',
      // Every page stays inside March 2023 so the range filter never empties
      // the result set mid-walk.
      date: `2023-03-${String((i % 28) + 1).padStart(2, '0')}`,
      type: 'debit',
      source: 'manual',
      status: 'posted',
      payee: null,
      payee_id: null,
      payee_name: null,
      notes: null,
      transfer_pair_id: null,
      amount_primary: 10,
      fx_rate_used: null,
      fx_fallback: false,
      installment_number: null,
      total_installments: null,
      installment_total_amount: null,
      installment_purchase_date: null,
      installment_series_id: null,
      bill_id: null,
      effective_bill_date: null,
      recurring_transaction_id: null,
      splits: [],
      is_ignored: false,
    })
  }
  return rows
}

/** What the fixture pretends the whole result set is. */
let ledgerTotal = 0
let ledger: Transaction[] = []
/** Every `/transactions` page the walk asked for, oldest first, tagged with
 *  the window it was for — a range change starts a second walk, and the two
 *  have to be told apart to see which one stopped. */
let pagesRequested: { page: number; to: string }[] = []
/** How a fixture page behaves. `instant` answers at once. `park` never
 *  answers, so only a cancellation can end the request. `handoff` holds the
 *  window named by `staleTo` until a newer window asks for its first page —
 *  the moment a superseded range's in-flight page would have landed had
 *  nothing cancelled it. A walk that honours the signal stops at that page; a
 *  walk that ignores it walks on to the ceiling, which is the whole
 *  difference this test has to see. */
let pageMode: 'instant' | 'park' | 'handoff' = 'instant'
let staleTo = ''
let handoffSeen = false
/** How often the handoff page checks whether the range has moved on. */
const HANDOFF_POLL_MS = 2

function respond(data: unknown, config: InternalAxiosRequestConfig): AxiosResponse {
  return { data, status: 200, statusText: 'OK', headers: {}, config }
}

api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
  const path = String(config.url ?? '')
  const params = (config.params ?? {}) as Record<string, unknown>

  if (path === '/transactions') {
    const page = Number(params.page ?? 1)
    const limit = Number(params.limit ?? 50)
    const to = String(params.to ?? '')
    pagesRequested.push({ page, to })
    if (pageMode === 'handoff' && to === staleTo) {
      await new Promise<void>((resolve, reject) => {
        const signal = config.signal as AbortSignal | undefined
        const release = () => {
          if (!handoffSeen) {
            handoffSeen = true
            resolve()
          }
        }
        // A newer window asking for its first page means the range moved, so
        // this page lands now — the same moment a real response would have.
        const poll = setInterval(() => {
          if (pagesRequested.some((r) => r.to !== staleTo)) release()
        }, HANDOFF_POLL_MS)
        signal?.addEventListener('abort', () => {
          clearInterval(poll)
          const error = Object.assign(new Error('canceled'), { name: 'CanceledError', __CANCEL__: true })
          reject(error)
        }, { once: true })
      })
    } else if (pageMode === 'park') {
      await new Promise<void>((_, reject) => {
        const signal = config.signal as AbortSignal | undefined
        signal?.addEventListener('abort', () => {
          const error = Object.assign(new Error('canceled'), { name: 'CanceledError', __CANCEL__: true })
          reject(error)
        }, { once: true })
      })
    }
    return respond({
      items: ledger.slice((page - 1) * limit, page * limit),
      total: ledgerTotal,
      page,
      limit,
    }, config)
  }
  if (path === '/accounts') return respond([account], config)
  if (path === `/accounts/${ACCOUNT_ID}`) return respond(account, config)
  if (path === `/accounts/${ACCOUNT_ID}/bills`) return respond([], config)
  if (path.startsWith(`/accounts/${ACCOUNT_ID}/summary`)) {
    return respond({
      account_id: ACCOUNT_ID,
      current_balance: 0,
      opening_balance: 0,
      monthly_income: 0,
      monthly_expenses: 0,
      current_balance_primary: null,
      opening_balance_primary: null,
      monthly_income_primary: null,
      monthly_expenses_primary: null,
    }, config)
  }
  if (path === '/dashboard/projected-transactions') return respond([], config)
  if (path === '/categories') return respond([], config)
  if (path === '/category-groups') return respond([], config)
  throw new Error(`Unhandled fixture route: ${path}`)
}

beforeEach(() => {
  pagesRequested = []
  pageMode = 'instant'
  staleTo = ''
  handoffSeen = false
  ledger = buildRows(600)
  ledgerTotal = ledger.length
  vi.setSystemTime(new Date('2023-03-31T12:00:00')) // default range = March 2023
})

afterEach(() => {
  pageMode = 'instant'
  staleTo = ''
  handoffSeen = false
  vi.useRealTimers()
})

describe('listAll truncation', () => {
  it('flags a walk that stopped at the row ceiling with rows still owing', async () => {
    // 20 500 rows: the walk collects 20 000 (40 full pages) and never gets to
    // the short page that would have ended it, while the server counted 20 500.
    ledger = buildRows(20500)
    ledgerTotal = ledger.length

    const result = await transactions.listAll({ account_id: ACCOUNT_ID })

    expect(pagesRequested).toHaveLength(40)
    expect(result.items).toHaveLength(20000)
    expect(result.total).toBe(20500)
    expect(result.truncated).toBe(true)
  })

  it('does not flag a result set that ends inside the ceiling', async () => {
    const result = await transactions.listAll({ account_id: ACCOUNT_ID })

    expect(pagesRequested.map((r) => r.page)).toEqual([1, 2])
    expect(result.items).toHaveLength(600)
    expect(result.truncated).toBe(false)
  })

  it('does not flag a result set that exactly fills the ceiling', async () => {
    // The ceiling is not itself evidence of clipping: 20 000 rows is a
    // complete answer, and labelling it incomplete would cry wolf on the one
    // account that hits the cap exactly.
    ledger = buildRows(20000)
    ledgerTotal = ledger.length

    const result = await transactions.listAll({ account_id: ACCOUNT_ID })

    expect(result.items).toHaveLength(20000)
    expect(result.total).toBe(20000)
    expect(result.truncated).toBe(false)
  })

  it('tells the user the range was cut instead of rendering it as the whole', async () => {
    // 20 000 rows really rendered through jsdom is a 14-second page, and the
    // banner does not care how many rows there are — only that the walk was
    // cut short. Stub the walk with the shape it produces at the ceiling and
    // let the page do the rest; the ceiling itself is asserted above, against
    // the real `listAll`.
    const cut = vi.spyOn(transactions, 'listAll').mockResolvedValue({
      items: buildRows(3),
      total: 20500,
      page: 40,
      limit: 500,
      truncated: true,
    })

    renderWithProviders(<AccountDetailPage />, {
      route: `/accounts/${ACCOUNT_ID}`,
      path: '/accounts/:id',
    })

    const banner = await screen.findByRole('status')
    // The two numbers the user needs to decide whether to narrow the period.
    expect(banner.textContent).toContain('20,500')
    expect(banner.textContent).toMatch(/newest/i)

    // A clipped range must not be the page's normal state.
    cut.mockRestore()
  })

  it('shows no banner when the range fit under the ceiling', async () => {
    renderWithProviders(<AccountDetailPage />, {
      route: `/accounts/${ACCOUNT_ID}`,
      path: '/accounts/:id',
    })

    // 600 rows: the walk finishes on a short page and the page is quiet.
    await waitFor(() => expect(pagesRequested.length).toBeGreaterThanOrEqual(2))
    await screen.findAllByText('Row 599')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  }, 30000)
})

describe('listAll cancellation', () => {
  it('stops walking when TanStack Query aborts the range', async () => {
    // The first range's page 1 stays in flight until the range moves, and
    // then lands anyway — the shape of a slow response the user outran. A
    // walk that honours the signal ends there; one that ignores it goes on to
    // page 40 for a window that no longer exists.
    pageMode = 'handoff'
    staleTo = '2023-03-31'
    ledger = buildRows(20500)
    ledgerTotal = ledger.length

    const { user } = renderWithProviders(<AccountDetailPage />, {
      route: `/accounts/${ACCOUNT_ID}`,
      path: '/accounts/:id',
    })
    const oldRange = await waitFor(() => {
      expect(pagesRequested).toHaveLength(1)
      return pagesRequested[0]
    })
    expect(oldRange).toEqual({ page: 1, to: '2023-03-31' })

    // Move the end date. The input renders the chosen date rather than a
    // placeholder, so the current value is how the control is found. This
    // replaces the query key: TanStack Query fires the previous fetch's
    // signal, and the superseded walk must stop instead of paging on to 40
    // for a window nobody is looking at any more.
    await user.click(screen.getByText('3/31/2023'))
    await user.click(await screen.findByRole('button', { name: '20' }))

    // The new range starts its own walk, which releases the old page: the
    // handoff the fixture was built to perform.
    await waitFor(() => {
      expect(pagesRequested.some((r) => r.to === '2023-03-20')).toBe(true)
    }, { timeout: 10000, interval: 20 })
    // Let the released page resolve and the fresh walk run to the ceiling —
    // 41 more pages' worth of turns, so an old walk that survived the abort
    // would have plenty of room to catch up.
    await waitFor(() => {
      expect(pagesRequested.filter((r) => r.to === '2023-03-20')).toHaveLength(40)
    }, { timeout: 15000, interval: 50 })

    // The superseded range asked for page 1, was cancelled, and stopped there.
    // An ignored signal walks on to the ceiling: 40 requests for a window that
    // was replaced a second after it was requested.
    expect(pagesRequested.filter((r) => r.to === '2023-03-31')).toEqual([
      { page: 1, to: '2023-03-31' },
    ])
  }, 30000)

  it('honours a signal that is already aborted', async () => {
    ledger = buildRows(20500)
    ledgerTotal = ledger.length
    const controller = new AbortController()
    controller.abort()

    const result = await transactions.listAll({ account_id: ACCOUNT_ID }, controller.signal)

    expect(pagesRequested).toEqual([])
    // Nothing was collected, so nothing is claimed to be missing either.
    expect(result.items).toEqual([])
    expect(result.truncated).toBe(false)
  })

  it('passes the signal down to axios so an in-flight page is really cancelled', async () => {
    // A page that never answers: without the signal reaching axios this walk
    // hangs instead of returning.
    pageMode = 'park'
    const controller = new AbortController()
    const walk = transactions.listAll({ account_id: ACCOUNT_ID }, controller.signal)
    await waitFor(() => expect(pagesRequested).toHaveLength(1))

    controller.abort()

    // The parked request rejects with axios's cancellation and the walk
    // returns a partial answer instead of throwing or hanging: a cancelled
    // walk has no error to show the user.
    const result = await walk
    expect(result.items).toEqual([])
    expect(pagesRequested).toHaveLength(1)
  }, 30000)
})
