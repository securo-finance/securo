import { screen, waitFor, within } from '@testing-library/react'
import { toast } from 'sonner'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import NotificationsPage from '@/pages/notifications'
import { i18n, renderWithProviders, t } from '@/test/utils'
import type { AppNotification } from '@/types'

const navigate = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}))
const workspace = vi.hoisted(() => ({ id: 'ws-1' }))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ current: { id: workspace.id, name: 'Home' } }),
}))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))

const api = vi.hoisted(() => ({ list: vi.fn(), markRead: vi.fn(), markAllRead: vi.fn(), preferences: vi.fn(), setPreference: vi.fn() }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  notifications: api,
}))

const day = (ago: number) => {
  const date = new Date()
  date.setHours(12, 0, 0, 0)
  date.setDate(date.getDate() - ago)
  return date.toISOString()
}
const bank = (id: string, ago: number, extra: Partial<AppNotification> = {}) =>
  ({
    id, notified_at: day(ago), read_at: null, resolved_at: null, kind: 'connection_error',
    data: { name: `Bank ${id}`, connection_id: id, code: 'consent_expired' }, ...extra,
  }) as AppNotification

const ALL = [bank('a', 0), bank('b', 1, { read_at: day(0) }), bank('c', 40, { resolved_at: '2026-11-20T12:00:00Z' })]

beforeEach(() => {
  api.list.mockImplementation(async ({ status }: { status: string }) => {
    const items = status === 'unread' ? [ALL[0]] : ALL
    return { items, total: items.length, unread_count: 1 }
  })
  api.markRead.mockResolvedValue(undefined)
  api.markAllRead.mockResolvedValue(undefined)
})
afterEach(() => vi.clearAllMocks())

it('lists them by day, past ones included', async () => {
  renderWithProviders(<NotificationsPage />)
  const today = await screen.findByRole('region', { name: 'Today' })
  expect(within(today).getByText('Bank a stopped syncing')).toBeInTheDocument()
  expect(within(screen.getByRole('region', { name: 'Yesterday' })).getByText('Bank b stopped syncing')).toBeInTheDocument()
  expect(screen.getByText('Bank c stopped syncing')).toBeInTheDocument()
  // Only what is open and unread can be marked.
  expect(screen.getAllByRole('button', { name: t('notifications.markRead') })).toHaveLength(1)
})

it('names the day in the app language, whatever the date format', async () => {
  await i18n.changeLanguage('pt-BR')
  try {
    renderWithProviders(<NotificationsPage />)
    expect(await screen.findByRole('region', { name: 'Hoje' })).toBeInTheDocument()
  } finally {
    await i18n.changeLanguage('en')
  }
})

it('marks one read without leaving the page, and follows one by its row', async () => {
  const { user } = renderWithProviders(<NotificationsPage />)
  await user.click(await screen.findByRole('button', { name: t('notifications.markRead') }))
  expect(api.markRead.mock.calls[0][0]).toBe('a')
  expect(navigate).not.toHaveBeenCalled()
  await user.click(screen.getByText('Bank b stopped syncing'))
  expect(navigate).toHaveBeenCalledWith('/accounts')
})

it('filters the unread ones', async () => {
  const { user } = renderWithProviders(<NotificationsPage />)
  await screen.findByText('Bank b stopped syncing')
  await user.click(screen.getByRole('tab', { name: new RegExp(`^${t('notifications.filter.unread')}`) }))
  await waitFor(() => expect(screen.queryByText('Bank b stopped syncing')).not.toBeInTheDocument())
  expect(screen.getByText('Bank a stopped syncing')).toBeInTheDocument()
  expect(api.list).toHaveBeenCalledWith({ status: 'unread', limit: 30, offset: 0 })
})

it('says when there is nothing left to read', async () => {
  api.list.mockResolvedValue({ items: [], total: 0, unread_count: 0 })
  const { user } = renderWithProviders(<NotificationsPage />)
  expect(await screen.findByText(t('notifications.emptyFilter.all'))).toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: t('notifications.filter.unread') }))
  expect(await screen.findByText(t('notifications.emptyFilter.unread'))).toBeInTheDocument()
})

it('loads the next page, listing a row that moved between pages once', async () => {
  api.list.mockImplementation(async ({ offset }: { offset: number }) => ({
    items: offset === 0 ? [bank('a', 0), bank('b', 0)] : [bank('b', 0), bank('c', 0)], total: 4, unread_count: 4,
  }))
  const { user } = renderWithProviders(<NotificationsPage />)
  await user.click(await screen.findByRole('button', { name: t('notifications.loadMore') }))
  expect(await screen.findByText('Bank c stopped syncing')).toBeInTheDocument()
  expect(screen.getAllByText('Bank b stopped syncing')).toHaveLength(1)
})

it('offers a retry when they cannot be loaded', async () => {
  api.list.mockRejectedValue(new Error('down'))
  renderWithProviders(<NotificationsPage />)
  expect(await screen.findByText(t('notifications.loadError'))).toBeInTheDocument()
  expect(screen.getByRole('button', { name: t('common.retry') })).toBeInTheDocument()
})

it('turns a kind off and on, one switch each', async () => {
  const saved = { budget_threshold: true, flag_overdue: false }
  api.preferences.mockImplementation(async () =>
    Object.entries(saved).map(([kind, enabled]) => ({ kind, channels: { in_app: enabled } })),
  )
  api.setPreference.mockImplementation(async ({ kind, enabled }: { kind: keyof typeof saved; enabled: boolean }) => {
    saved[kind] = enabled
  })
  const { user } = renderWithProviders(<NotificationsPage />, { route: '/notifications?tab=preferences' })
  const budgets = await screen.findByRole('switch', { name: t('notifications.kind.budget_threshold.label') })
  const reminders = screen.getByRole('switch', { name: t('notifications.kind.flag_overdue.label') })
  expect(budgets).toBeChecked()
  expect(reminders).not.toBeChecked()
  await user.click(budgets)
  expect(api.setPreference.mock.calls[0][0]).toEqual({ kind: 'budget_threshold', channel: 'in_app', enabled: false, workspaceId: 'ws-1' })
  await waitFor(() => expect(budgets).not.toBeChecked())
  expect(reminders).not.toBeChecked()
  expect(saved).toEqual({ budget_threshold: false, flag_overdue: false })
})

it('puts a switch back when it could not be saved', async () => {
  api.preferences.mockResolvedValue([{ kind: 'budget_threshold', channels: { in_app: true } }])
  let fail = () => {}
  api.setPreference.mockImplementation(
    () => new Promise<void>((_, reject) => { fail = () => reject(new Error('down')) }),
  )
  const { user } = renderWithProviders(<NotificationsPage />, { route: '/notifications?tab=preferences' })
  const budgets = await screen.findByRole('switch')
  await user.click(budgets)
  await waitFor(() => expect(budgets).not.toBeChecked())
  // Asking again never answers, so only the rollback can put it back.
  api.preferences.mockImplementation(() => new Promise(() => {}))
  fail()
  await waitFor(() => expect(budgets).toBeChecked())
})

it('saves clicks on a switch one at a time, so the last one stands', async () => {
  const saved = { budget_threshold: true }
  const releases: (() => void)[] = []
  api.preferences.mockImplementation(async () => [
    { kind: 'budget_threshold', channels: { in_app: saved.budget_threshold } },
  ])
  api.setPreference.mockImplementation(
    ({ enabled }: { enabled: boolean }) =>
      new Promise<void>((resolve) => {
        releases.push(() => {
          saved.budget_threshold = enabled
          resolve()
        })
      }),
  )
  const { user } = renderWithProviders(<NotificationsPage />, { route: '/notifications?tab=preferences' })
  const budgets = await screen.findByRole('switch')
  await user.click(budgets)
  await user.click(budgets)
  expect(releases).toHaveLength(1)
  releases[0]()
  await waitFor(() => expect(releases).toHaveLength(2))
  releases[1]()
  await waitFor(() => expect(api.preferences).toHaveBeenCalledTimes(2))
  expect(saved.budget_threshold).toBe(true)
  expect(budgets).toBeChecked()
})

it('saves a click that waited in the workspace it was made in', async () => {
  const releases: (() => void)[] = []
  api.preferences.mockResolvedValue([{ kind: 'budget_threshold', channels: { in_app: true } }])
  api.setPreference.mockImplementation(() => new Promise<void>((resolve) => releases.push(resolve)))
  const { user, rerender } = renderWithProviders(<NotificationsPage />, { route: '/notifications?tab=preferences' })
  const budgets = await screen.findByRole('switch')
  await user.click(budgets)
  await user.click(budgets)
  try {
    workspace.id = 'ws-2'
    rerender(<NotificationsPage />)
    releases[0]()
    await waitFor(() => expect(api.setPreference).toHaveBeenCalledTimes(2))
    expect(api.setPreference.mock.calls.map(([change]) => change.workspaceId)).toEqual(['ws-1', 'ws-1'])
    releases[1]()
  } finally {
    workspace.id = 'ws-1'
  }
})

it('says when one could not be marked as read', async () => {
  api.markRead.mockRejectedValue(new Error('down'))
  const error = vi.spyOn(toast, 'error')
  const { user } = renderWithProviders(<NotificationsPage />)
  await user.click(await screen.findByRole('button', { name: t('notifications.markRead') }))
  await waitFor(() => expect(error).toHaveBeenCalledWith(t('common.error')))
})

it('writes and links the kinds that count', async () => {
  const items = [
    { ...bank('f', 0), kind: 'flag_overdue', data: { count: 3 } },
    { ...bank('m', 0), kind: 'reconciliation_pending', data: { count: 2 } },
    {
      ...bank('i', 0), kind: 'invoice_overdue',
      data: { invoice_id: 'inv', name: 'Acme', date: '2026-11-20', amount: '1000.00', currency: 'USD' },
    },
  ] as AppNotification[]
  api.list.mockResolvedValue({ items, total: 3, unread_count: 3 })
  const { user } = renderWithProviders(<NotificationsPage />)
  expect(await screen.findByText('$1,000.00, due on 11/20/2026.')).toBeInTheDocument()
  for (const [text, path] of [
    ['Overdue reminders: 3', '/transactions?flagged=overdue'],
    ['Matches to review: 2', '/rules?tab=queue'],
    ['Acme: invoice overdue', '/invoices/inv'],
  ]) {
    await user.click(screen.getByText(text))
    expect(navigate).toHaveBeenLastCalledWith(path)
  }
})

it('tells what others did in a group, by what each share says', async () => {
  const group = { group_id: 'g1', group: 'Trip', actor: 'Ana' }
  const expense = { ...group, transaction_id: 't', name: 'Dinner', share: '25.00', currency: 'USD', changed: false }
  const items = [
    { ...bank('1', 0), kind: 'group_expense', data: expense },
    { ...bank('2', 0), kind: 'group_expense', data: { ...expense, name: 'Taxi', changed: true } },
    { ...bank('3', 0), kind: 'group_expense', data: { ...group, count: 3 } },
    {
      ...bank('4', 0), kind: 'group_settlement',
      data: { ...group, settlement_id: 's', payer: 'Ana', receiver: 'Me', amount: '40.00', currency: 'USD' },
    },
  ] as AppNotification[]
  api.list.mockResolvedValue({ items, total: 4, unread_count: 4 })
  const { user } = renderWithProviders(<NotificationsPage />)
  expect(await screen.findByText('Ana added Dinner')).toBeInTheDocument()
  expect(screen.getAllByText('Your share in Trip: $25.00')).toHaveLength(2)
  expect(screen.getByText('Ana changed Taxi')).toBeInTheDocument()
  expect(screen.getByText('Ana added shared expenses: 3')).toBeInTheDocument()
  expect(screen.getByText(/Ana . Me: \$40\.00 · Trip/)).toBeInTheDocument()
  await user.click(screen.getByText('Ana recorded a payment'))
  expect(navigate).toHaveBeenLastCalledWith('/groups/g1')
})

it('still lists the rest next to a kind newer than this bundle', async () => {
  const newer = { ...bank('z', 0), kind: 'from_a_newer_server', data: {} } as unknown as AppNotification
  api.list.mockResolvedValue({ items: [newer, bank('a', 0)], total: 2, unread_count: 2 })
  renderWithProviders(<NotificationsPage />)
  expect(await screen.findByText('Bank a stopped syncing')).toBeInTheDocument()
  expect(screen.getAllByRole('listitem')).toHaveLength(2)
})

it('leaves a kind newer than this bundle out of the switches', async () => {
  api.preferences.mockResolvedValue([
    { kind: 'from_a_newer_server', channels: { in_app: true } },
    { kind: 'budget_threshold', channels: { in_app: true } },
  ])
  renderWithProviders(<NotificationsPage />, { route: '/notifications?tab=preferences' })
  expect(await screen.findAllByRole('switch')).toHaveLength(1)
})
