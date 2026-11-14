import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import type { InternalAxiosRequestConfig } from 'axios'
import TransactionsPage from '@/pages/transactions'
import { api } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'

const state = vi.hoisted(() => ({ ids: null as string[] | null }))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: state.ids }),
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: 'synthetic-user', preferences: { currency_display: 'USD' } } }),
}))
vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/contexts/workspace-context')>(),
  useWorkspace: () => ({ canWrite: false, hasModule: () => false }),
}))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US',
}))

const originalAdapter = api.defaults.adapter
let calls: InternalAxiosRequestConfig[]
beforeEach(() => {
  state.ids = null
  calls = []
  api.defaults.adapter = async (config) => {
    if (config.method !== 'get') throw new Error('Read-only calendar must not write')
    calls.push(config)
    const data = config.url === '/transactions/calendar'
      ? { month: '2026-07', currency: 'USD', days: [], account_ids: config.params?.account_ids ?? null }
      : config.url === '/transactions' ? { items: [], total: 0 }
      : config.url?.includes('accounting-mode') ? { mode: 'cash' } : []
    return { data, status: 200, statusText: 'OK', headers: {}, config }
  }
})
afterEach(() => { api.defaults.adapter = originalAdapter })

function calendarCalls() {
  return calls.filter((call) => call.url === '/transactions/calendar')
}

it.each([
  { name: 'implicit selection', page: '', expected: undefined },
  { name: 'single page selection', page: '&account_id=flagged', expected: ['flagged'] },
  { name: 'multiple page selections are omitted', page: '&account_id=included,flagged', expected: undefined },
])('sends main single-page calendar selection: $name', async ({ page, expected }) => {
  renderWithProviders(<TransactionsPage />, {
    route: `/transactions?view=calendar&from=2026-07-01&to=2026-07-31${page}`,
  })
  await waitFor(() => expect(calendarCalls()).not.toHaveLength(0))
  await waitFor(() => expect(calendarCalls().at(-1)?.params?.account_ids).toEqual(expected))
  expect(calendarCalls().at(-1)?.params?.date_basis).toBe('reporting')
})

it('does not forward an active collection to the transactions calendar', async () => {
  state.ids = ['flagged']
  const view = renderWithProviders(<TransactionsPage />, {
    route: '/transactions?view=calendar&from=2026-07-01&to=2026-07-31',
  })
  await waitFor(() => expect(calendarCalls()).not.toHaveLength(0))
  expect(calendarCalls().at(-1)?.params?.account_ids).toBeUndefined()
  const keys = view.queryClient.getQueryCache().getAll()
    .filter((query) => query.queryKey[1] === 'calendar').map((query) => query.queryKey)
  expect(keys.some((key) => JSON.stringify(key.at(-2)) === '[]')).toBe(true)
})

it('keeps the date basis in the calendar request and query key', async () => {
  const view = renderWithProviders(<TransactionsPage />, {
    route: '/transactions?view=calendar&from=2026-07-01&to=2026-07-31',
  })
  await waitFor(() => expect(calendarCalls()).not.toHaveLength(0))
  expect(calendarCalls().at(-1)?.params?.date_basis).toBe('reporting')

  const { user } = view
  await user.click(screen.getByRole('button', { name: t('transactions.bankDate') }))
  await waitFor(() => expect(calendarCalls().at(-1)?.params?.date_basis).toBe('bank'))

  const keys = view.queryClient.getQueryCache().getAll()
    .filter((query) => query.queryKey[1] === 'calendar').map((query) => query.queryKey)
  expect(keys.some((key) => key.at(-1) === 'bank')).toBe(true)
})
