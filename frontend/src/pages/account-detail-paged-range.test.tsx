/**
 * The account page draws its balance chart, its running balance and its
 * transaction list from ONE query — and it used to ask that query for a single
 * page of `limit: 500`. On an account whose history is longer than 500 rows
 * everything older than the newest 500 silently disappeared: with the range
 * widened back to 03/2023 the chart kept the 1310-day X axis but walked a
 * balance that had no transactions in it, so it drew a flat line on the
 * opening balance and the March 2023 income never appeared.
 *
 * This test runs the real `transactions.listAll` (no endpoint mocking: an
 * axios adapter serves a 620-row month, so page 2 has to be requested) and
 * asserts both halves of the symptom:
 *   - the oldest rows — the 05.03.2023 income sitting beyond the first page —
 *     are in the rendered list, and
 *   - the chart's value axis reaches that income's magnitude, which it cannot
 *     do while only page 1 is fetched.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'

import AccountDetailPage from '@/pages/account-detail'
import { renderWithProviders } from '@/test/utils'
import { api } from '@/lib/api'
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
const INCOME_DATE = '2023-03-05'
const INCOME_DESC = 'Salary March 2023'
const INCOME_AMOUNT = 10000

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

/** Whole-month ledger, newest first, exactly like the API's default order. */
function buildLedger(): Transaction[] {
  const rows: Transaction[] = []
  const count = 620
  for (let i = 0; i < count; i++) {
    const day = 31 - Math.floor((i * 31) / count)
    const isIncome = i === 530 // lands on 05.03 → page 2, never on page 1
    rows.push({
      id: `tx-${i}`,
      user_id: 'user-1',
      account_id: ACCOUNT_ID,
      category_id: null,
      category: null,
      external_id: null,
      description: isIncome ? INCOME_DESC : `Coffee ${i}`,
      original_description: null,
      amount: isIncome ? INCOME_AMOUNT : 10,
      currency: 'USD',
      date: isIncome ? INCOME_DATE : `2023-03-${String(day).padStart(2, '0')}`,
      type: isIncome ? 'credit' : 'debit',
      source: 'manual',
      status: 'posted',
      payee: null,
      payee_id: null,
      payee_name: null,
      notes: null,
      transfer_pair_id: null,
      amount_primary: isIncome ? INCOME_AMOUNT : 10,
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
  expect(rows[530].date).toBe(INCOME_DATE)
  expect(rows.slice(0, 500).some((tx) => tx.date === INCOME_DATE)).toBe(false)
  return rows
}

const ledger = buildLedger()

/** Every `/api/...` path the page hit, in order (transactions: page numbers). */
const requests: { path: string; params: Record<string, unknown> }[] = []

function respond(data: unknown, config: InternalAxiosRequestConfig): AxiosResponse {
  return { data, status: 200, statusText: 'OK', headers: {}, config }
}

api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
  const path = String(config.url ?? '')
  const params = (config.params ?? {}) as Record<string, unknown>
  requests.push({ path, params })

  if (path === '/transactions') {
    const page = Number(params.page ?? 1)
    const limit = Number(params.limit ?? 50)
    return respond({ items: ledger.slice((page - 1) * limit, page * limit), total: ledger.length, page, limit }, config)
  }
  if (path === '/accounts') return respond([account], config)
  if (path === `/accounts/${ACCOUNT_ID}`) return respond(account, config)
  if (path === `/accounts/${ACCOUNT_ID}/bills`) return respond([], config)
  if (path.startsWith(`/accounts/${ACCOUNT_ID}/summary`)) {
    return respond({
      account_id: ACCOUNT_ID,
      current_balance: 3810,
      opening_balance: 0,
      monthly_income: INCOME_AMOUNT,
      monthly_expenses: 6190,
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
  requests.length = 0
  vi.setSystemTime(new Date('2023-03-31T12:00:00')) // default range = March 2023
})

afterEach(() => {
  vi.useRealTimers()
})

/** Recharts writes value-axis ticks as text; strip the currency decoration. */
function valueAxisTicks(container: HTMLElement): number[] {
  return Array.from(container.querySelectorAll('.recharts-yAxis-tick-labels text'))
    .map((node) => Number((node.textContent ?? '').replace(/[^\d-]/g, '')))
    .filter((value) => Number.isFinite(value))
}

describe('account balance history over a range longer than one page', () => {
  it('walks every page so the March 2023 income reaches the list and the chart', async () => {
    const { container } = renderWithProviders(<AccountDetailPage />, {
      route: `/accounts/${ACCOUNT_ID}`,
      path: '/accounts/:id',
    })

    await waitFor(() => expect(screen.getByText(INCOME_DESC)).toBeInTheDocument())

    // 620 rows / 500 per page → exactly two requests, both for the same window.
    const txPages = requests
      .filter((r) => r.path === '/transactions')
      .map((r) => r.params.page)
    expect(txPages).toEqual([1, 2])
    expect(requests.filter((r) => r.path === '/transactions')[0].params).toMatchObject({
      account_id: ACCOUNT_ID,
      from: '2023-03-01',
      to: '2023-03-31',
      limit: 500,
    })

    // The chart walks the same balance as the list, so its value axis can only
    // reach the income's magnitude when page 2 was fetched. Truncated to page
    // 1 the axis tops out at $0 — the flat line on the opening balance that
    // sent this bug here. Recharts paints the ticks on a layout pass, so give
    // it a generous window: under a parallel suite the chart can take seconds.
    await waitFor(() => expect(valueAxisTicks(container).length).toBeGreaterThan(0), { timeout: 10000, interval: 100 })
    expect(Math.max(...valueAxisTicks(container))).toBeGreaterThanOrEqual(1000)
  }, 30000)
})
