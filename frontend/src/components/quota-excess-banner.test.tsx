import { act, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { QuotaExcessBanner } from '@/components/quota-excess-banner'
import { describeNotification, notificationTarget, quotaExcessLines } from '@/lib/notification-text'
import { createTestQueryClient, i18n, renderWithProviders } from '@/test/utils'
import type { AppNotification, QuotaCounterExcess, QuotaStanding } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))

const api = vi.hoisted(() => ({ mine: vi.fn() }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  quotas: api,
}))

afterEach(() => {
  vi.clearAllMocks()
})

const connections: QuotaCounterExcess = { counter: 'max_bank_connections', current: 3, maximum: 1, excess: 2 }
const members: QuotaCounterExcess = { counter: 'max_members_per_workspace', current: 4, maximum: 3, excess: 1 }
const fits: QuotaCounterExcess = { counter: 'max_personal_workspaces', current: 1, maximum: 2, excess: 0 }
const t = i18n.t.bind(i18n)
const text = { t, date: (iso: string) => iso.slice(0, 10), money: (a: string) => a }
const base = { id: 'q', notified_at: '2026-11-24T10:00:00Z', read_at: null, resolved_at: null }

it('lists only the limits the account is above, with how many to remove', () => {
  expect(quotaExcessLines(t, [connections, fits, members])).toEqual([
    'Bank connections: 3 of 1, remove 2',
    'Members in a workspace: 4 of 3, remove 1',
  ])
})

it('tells the date before it comes, and the pause once it has', () => {
  const upcoming: AppNotification = {
    ...base,
    kind: 'quota_excess',
    data: { effective_at: '2026-12-15T00:00:00Z', in_effect: false, sync_paused: false, counters: [connections] },
  }
  expect(describeNotification(upcoming, text)).toEqual({
    title: 'Your account is above its limits from 2026-12-15',
    detail: 'Bank connections: 3 of 1, remove 2',
  })
  const paused: AppNotification = { ...upcoming, data: { ...upcoming.data, in_effect: true, sync_paused: true } }
  expect(describeNotification(paused, text)).toEqual({
    title: 'Your account is above its limits',
    detail: 'Bank sync is paused until you are within your limit. Bank connections: 3 of 1, remove 2',
  })
  expect(notificationTarget(paused)).toBe('/accounts')
  expect(notificationTarget({ ...paused, data: { ...paused.data, counters: [members] } })).toBe('/notifications')
})

const standing: QuotaStanding = {
  above: true,
  effective_at: '2026-11-20T00:00:00Z',
  in_effect: true,
  sync_paused: true,
  counters: [connections, fits],
}

it('shows what is above the limit and that sync is paused', async () => {
  api.mine.mockResolvedValue(standing)
  renderWithProviders(<QuotaExcessBanner />)
  expect(await screen.findByText('Your account is above its limits')).toBeInTheDocument()
  expect(screen.getByText('Bank sync is paused until you are within your limit.')).toBeInTheDocument()
  expect(screen.getByText('Bank connections: 3 of 1, remove 2')).toBeInTheDocument()
  expect(screen.queryByText(/Personal workspaces/)).not.toBeInTheDocument()
})

it('shows nothing without a date, or off a managed instance', async () => {
  for (const answer of [{ ...standing, effective_at: null, in_effect: false, sync_paused: false }, null]) {
    api.mine.mockResolvedValue(answer)
    const queryClient = createTestQueryClient()
    const { container, unmount } = renderWithProviders(<QuotaExcessBanner />, { queryClient })
    // Past the answer's own render, so the date guard is what keeps it empty.
    await vi.waitFor(() => expect(queryClient.getQueryState(['quotas', 'mine'])?.status).toBe('success'))
    await act(async () => {})
    expect(container).toBeEmptyDOMElement()
    unmount()
  }
  expect(api.mine).toHaveBeenCalledTimes(2)
})

it('refreshes the standing while it is shown', async () => {
  vi.useFakeTimers()
  try {
    api.mine.mockResolvedValue(standing)
    renderWithProviders(<QuotaExcessBanner />)
    await vi.advanceTimersByTimeAsync(0)
    expect(api.mine).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000)
    expect(api.mine).toHaveBeenCalledTimes(2)
  } finally {
    vi.useRealTimers()
  }
})
