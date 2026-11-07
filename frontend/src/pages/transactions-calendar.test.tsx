import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
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

it.each([
  { name: 'implicit', collection: null, page: '', expected: undefined },
  { name: 'flagged collection', collection: ['flagged'], page: '', expected: ['flagged'] },
  { name: 'mixed collection', collection: ['included', 'flagged'], page: '', expected: ['included', 'flagged'] },
  { name: 'page selection wins', collection: ['included'], page: '&account_id=flagged', expected: ['flagged'] },
  { name: 'multiple page selections win', collection: ['other'], page: '&account_id=included,flagged', expected: ['included', 'flagged'] },
])('passes original selection to real calendar transport: $name', async ({ collection, page, expected }) => {
  state.ids = collection
  const view = renderWithProviders(<TransactionsPage />, {
    route: `/transactions?view=calendar&from=2026-07-01&to=2026-07-31${page}`,
  })
  await waitFor(() => expect(calls.filter((call) => call.url === '/transactions/calendar')).not.toHaveLength(0))
  await waitFor(() => expect(calls.filter((call) => call.url === '/transactions/calendar').at(-1)?.params?.account_ids).toEqual(expected))
  const keys = view.queryClient.getQueryCache().getAll()
    .filter((query) => query.queryKey[1] === 'calendar').map((query) => query.queryKey)
  expect(keys.some((key) => JSON.stringify(key.at(-1)) === JSON.stringify(expected ?? null))).toBe(true)
})

it('separates cached collection scopes and never requests an empty collection', async () => {
  state.ids = ['flagged']
  const view = renderWithProviders(<TransactionsPage />, { route: '/transactions?view=calendar' })
  await waitFor(() => expect(calls.some((call) => call.url === '/transactions/calendar')).toBe(true))
  state.ids = ['included']
  await act(async () => { view.rerender(<TransactionsPage />) })
  await waitFor(() => expect(calls.filter((call) => call.url === '/transactions/calendar').at(-1)?.params?.account_ids).toEqual(['included']))
  const count = calls.filter((call) => call.url === '/transactions/calendar').length
  state.ids = []
  await act(async () => { view.rerender(<TransactionsPage />) })
  expect(calls.filter((call) => call.url === '/transactions/calendar')).toHaveLength(count)
  expect(screen.queryByRole('button', { name: t('common.refresh') })).not.toBeInTheDocument()
  const emptyScope = view.queryClient.getQueryCache().getAll().find((query) => (
    query.queryKey[1] === 'calendar' && JSON.stringify(query.queryKey.at(-1)) === '[]'
  ))
  expect(emptyScope).toBeDefined()
  expect(emptyScope?.state.data).toBeUndefined()
})
