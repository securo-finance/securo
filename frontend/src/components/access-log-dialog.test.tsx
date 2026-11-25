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

  it('shows the address change and that it waits for the owner', async () => {
    api.accessLog.mine.mockResolvedValue({
      items: [
        entry({
          changes: {
            email: {
              from: 'someone@example.com',
              to: 'elsewhere@example.com',
              pending: true,
            },
            is_active: { from: true, to: false },
          },
        }),
      ],
      total: 1,
    })

    renderWithProviders(<AccessLogDialog open onClose={vi.fn()} />)

    expect(
      await screen.findByText(
        'Address someone@example.com → elsewhere@example.com · waiting for the account owner to confirm',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Also changed: is_active')).toBeInTheDocument()
  })

  it('says so when nothing was recorded', async () => {
    api.accessLog.mine.mockResolvedValue({ items: [], total: 0 })

    renderWithProviders(<AccessLogDialog open onClose={vi.fn()} />)

    expect(await screen.findByText('No administrative actions recorded.')).toBeInTheDocument()
  })

  it('appends the next page and stops when everything is loaded', async () => {
    api.accessLog.mine.mockImplementation(({ page }: { page: number }) =>
      Promise.resolve(
        page === 1
          ? { items: [entry({ id: 'log-1', reason: 'First page' })], total: 2 }
          : {
              items: [entry({ id: 'log-2', reason: 'Second page', target_email: 'other@example.com' })],
              total: 2,
            },
      ),
    )

    const { user } = renderWithProviders(<AccessLogDialog open onClose={vi.fn()} />)

    expect(await screen.findByText('First page')).toBeInTheDocument()
    expect(api.accessLog.mine).toHaveBeenCalledWith({ page: 1, limit: 50 })

    await user.click(screen.getByRole('button', { name: 'Load more' }))

    expect(await screen.findByText('Second page')).toBeInTheDocument()
    expect(screen.getByText('First page')).toBeInTheDocument()
    expect(api.accessLog.mine).toHaveBeenLastCalledWith({ page: 2, limit: 50 })
    // Every request stays inside the endpoint's ceiling, however much is left.
    for (const [params] of api.accessLog.mine.mock.calls) {
      expect(params.limit).toBeLessThanOrEqual(100)
    }
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })
})
