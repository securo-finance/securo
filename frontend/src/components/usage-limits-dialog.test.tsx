import { screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import { UsageLimitsDialog } from './usage-limits-dialog'

const api = vi.hoisted(() => ({ mine: vi.fn() }))
vi.mock('@/lib/api', () => ({ quotas: { mine: api.mine } }))

beforeEach(() => {
  vi.resetAllMocks()
})

it('lists each bounded counter with its count and maximum', async () => {
  api.mine.mockResolvedValue({
    enforced: true,
    info_url: null,
    counters: [
      { counter: 'max_personal_workspaces', current: 1, maximum: 2 },
      { counter: 'max_members_per_workspace', current: 3, maximum: 3 },
    ],
  })
  renderWithProviders(<UsageLimitsDialog open onClose={vi.fn()} />)

  expect(await screen.findByText(t('quota.counters.max_personal_workspaces'))).toBeInTheDocument()
  expect(screen.getByText(t('quota.usage', { current: 1, maximum: 2 }))).toBeInTheDocument()
  expect(screen.getByText(t('quota.counters.max_members_per_workspace'))).toBeInTheDocument()
  expect(screen.getByText(t('quota.membersHint'))).toBeInTheDocument()
  expect(screen.getByText(t('quota.usage', { current: 3, maximum: 3 }))).toBeInTheDocument()
  // Unbounded counters are not listed, and no link without the setting.
  expect(screen.queryByText(t('quota.counters.max_bank_connections'))).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: t('quota.learnMore') })).not.toBeInTheDocument()
})

it('links to the operator page when the instance sets one', async () => {
  api.mine.mockResolvedValue({
    enforced: true,
    info_url: '/help/limits',
    counters: [{ counter: 'max_bank_connections', current: 0, maximum: 1 }],
  })
  renderWithProviders(<UsageLimitsDialog open onClose={vi.fn()} />)

  expect(await screen.findByRole('link', { name: t('quota.learnMore') })).toHaveAttribute('href', '/help/limits')
})

it('says so when the limits cannot be read', async () => {
  api.mine.mockRejectedValue(new Error('offline'))
  renderWithProviders(<UsageLimitsDialog open onClose={vi.fn()} />)

  expect(await screen.findByText(t('quota.loadError'))).toBeInTheDocument()
  expect(screen.getByRole('button', { name: t('common.retry') })).toBeInTheDocument()
})
