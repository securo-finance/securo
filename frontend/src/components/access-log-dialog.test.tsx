import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { AccessLogDialog } from '@/components/access-log-dialog'
import { renderWithProviders } from '@/test/utils'
import type { AdminAccessLogEntry } from '@/types'

const api = vi.hoisted(() => ({
  accessLog: { mine: vi.fn() },
}))

vi.mock('@/lib/api', () => ({ accessLog: api.accessLog }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

function entry(overrides: Partial<AdminAccessLogEntry> = {}): AdminAccessLogEntry {
  return {
    id: 'log-1',
    actor_email: 'admin@example.com',
    target_email: 'someone@example.com',
    target_user_id: 'user-1',
    action: 'user.update',
    reason: 'Support ticket 12',
    ip: '203.0.113.7',
    created_at: '2026-11-25T12:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('AccessLogDialog', () => {
  it('shows what was done, why, who did it and from where', async () => {
    api.accessLog.mine.mockResolvedValue({ items: [entry()], total: 1 })

    renderWithProviders(<AccessLogDialog open onClose={vi.fn()} />)

    expect(await screen.findByText('Account updated')).toBeInTheDocument()
    expect(screen.getByText('Support ticket 12')).toBeInTheDocument()
    expect(screen.getByText('Account someone@example.com')).toBeInTheDocument()
    // The time is rendered in the machine's zone, so pin only who and where.
    const byline = screen.getByText(/By admin@example\.com/)
    expect(byline.textContent).toContain('IP 203.0.113.7')
  })

  it('says so when nothing was recorded', async () => {
    api.accessLog.mine.mockResolvedValue({ items: [], total: 0 })

    renderWithProviders(<AccessLogDialog open onClose={vi.fn()} />)

    expect(await screen.findByText('No administrative actions recorded.')).toBeInTheDocument()
  })

  it('loads the rest only when there is more', async () => {
    api.accessLog.mine.mockResolvedValue({ items: [entry()], total: 51 })

    const { user } = renderWithProviders(<AccessLogDialog open onClose={vi.fn()} />)

    const more = await screen.findByRole('button', { name: 'Load more' })
    await user.click(more)

    expect(api.accessLog.mine).toHaveBeenLastCalledWith({ limit: 100 })
  })
})
