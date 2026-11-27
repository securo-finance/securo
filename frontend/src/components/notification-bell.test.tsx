import { screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { NotificationBell } from '@/components/notification-bell'
import { renderWithProviders, t } from '@/test/utils'
import type { AppNotification } from '@/types'

const navigate = vi.hoisted(() => vi.fn())
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigate,
}))

vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ current: { id: 'ws-1' } }) }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))

const api = vi.hoisted(() => ({ list: vi.fn(), markRead: vi.fn(), markAllRead: vi.fn() }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  notifications: api,
}))

const base = { notified_at: '2026-11-24T10:00:00Z', read_at: null, resolved_at: null }
const ITEMS: AppNotification[] = [
  {
    ...base,
    id: 'c',
    kind: 'connection_error',
    data: { name: 'My Bank', connection_id: 'x', code: 'consent_expired' },
  },
  {
    ...base,
    id: 'b',
    kind: 'budget_threshold',
    data: {
      category_id: 'y',
      name: 'Food',
      month: '2026-11-01',
      threshold: 80,
      actual: '85.90',
      budget: '100.00',
      currency: 'USD',
    },
  },
  { ...base, id: 'r', kind: 'recurring_failed', data: { recurring_id: 'z', name: 'Rent', date: '2026-11-20' } },
  { ...base, id: 'k', kind: 'backup_failed', data: { failed_at: '2026-11-24T10:00:00Z' } },
]

function setup() {
  return renderWithProviders(<NotificationBell />)
}

beforeEach(() => {
  api.list.mockResolvedValue({ items: ITEMS, total: 4, unread_count: 4 })
  api.markRead.mockResolvedValue(undefined)
  api.markAllRead.mockResolvedValue(undefined)
})

afterEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

it('counts the unread ones and writes each from its facts', async () => {
  const { user } = setup()
  await user.click(await screen.findByRole('button', { name: t('notifications.unreadLabel', { number: 4 }) }))
  expect(screen.getByText('My Bank stopped syncing')).toBeInTheDocument()
  expect(screen.getByText(t('accounts.syncFailure.consent_expired'))).toBeInTheDocument()
  expect(screen.getByText('Food: 85% of the budget used')).toBeInTheDocument()
  expect(screen.getByText('$85.90 of $100.00')).toBeInTheDocument()
  // The day as stored, whatever the timezone of the browser.
  expect(screen.getByText(/11\/20\/2026/)).toBeInTheDocument()
})

it('hides amounts in privacy mode', async () => {
  localStorage.setItem('privacyMode', 'true')
  const { user } = setup()
  await user.click(await screen.findByRole('button', { name: /4 unread/ }))
  expect(screen.queryByText(/85\.90/)).not.toBeInTheDocument()
})

it('marks one read and goes to where it is fixed', async () => {
  const { user } = setup()
  await user.click(await screen.findByRole('button', { name: /4 unread/ }))
  await user.click(screen.getByText('My Bank stopped syncing'))
  expect(api.markRead.mock.calls[0][0]).toBe('c')
  expect(navigate).toHaveBeenCalledWith('/accounts')
})

it('goes to the backups page for a failed backup', async () => {
  const { user } = setup()
  await user.click(await screen.findByRole('button', { name: /4 unread/ }))
  await user.click(screen.getByText(t('notifications.backup.title')))
  expect(navigate).toHaveBeenCalledWith('/backups')
})

it('marks all read and asks again', async () => {
  const { user } = setup()
  await user.click(await screen.findByRole('button', { name: /4 unread/ }))
  api.list.mockResolvedValue({ items: [], total: 0, unread_count: 0 })
  await user.click(screen.getByRole('button', { name: t('notifications.markAllRead') }))
  expect(api.markAllRead).toHaveBeenCalled()
  await waitFor(() => expect(screen.getByText(t('notifications.empty'))).toBeInTheDocument())
})

it('leads to the page with all of them', async () => {
  const { user } = setup()
  await user.click(await screen.findByRole('button', { name: /4 unread/ }))
  await user.click(screen.getByRole('button', { name: t('notifications.seeAll') }))
  expect(navigate).toHaveBeenCalledWith('/notifications')
})

it('says when they could not be loaded', async () => {
  api.list.mockRejectedValue(new Error('down'))
  const { user } = setup()
  await user.click(await screen.findByRole('button', { name: t('notifications.title') }))
  expect(await screen.findByText(t('notifications.loadError'))).toBeInTheDocument()
})
