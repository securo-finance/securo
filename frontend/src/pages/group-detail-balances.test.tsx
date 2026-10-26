import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import GroupDetailPage from '@/pages/group-detail'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  balances: vi.fn(),
  settlements: vi.fn(),
  transactions: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  groups: {
    get: api.get,
    balances: api.balances,
    transactions: api.transactions,
    settlements: { list: api.settlements, create: vi.fn(), delete: vi.fn() },
    members: { create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  },
  accounts: { list: vi.fn().mockResolvedValue([]) },
  transactions: { list: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
}))

const auth = vi.hoisted(() => ({ userId: 'user-bob' }))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: auth.userId, preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

const member = (id: string, name: string, linked_user_id: string | null, is_self: boolean) => ({
  id, group_id: 'g1', name, linked_user_id, email: null, is_self, created_at: '2026-10-01T00:00:00Z',
})

// The server sets is_self per request: it marks the viewer's own member.
function group(viewer: 'alice' | 'bob') {
  return {
    id: 'g1', user_id: 'user-alice', name: 'Trip', kind: 'social', default_currency: 'USD',
    icon: 'users', color: '#123456', is_archived: false, is_owner: viewer === 'alice',
    notes: null, created_at: '2026-10-01T00:00:00Z',
    members: [
      member('m-alice', 'Alice', 'user-alice', viewer === 'alice'),
      member('m-bob', 'Bob', 'user-bob', viewer === 'bob'),
      member('m-carol', 'Carol', null, false),
    ],
  }
}

const line = (member_id: string, amount: number) => ({
  member_id, currency: 'USD', amount, amount_in_default_currency: amount,
})

function renderPage() {
  return renderWithProviders(<GroupDetailPage />, { route: '/groups/g1', path: '/groups/:id' })
}

function balanceRows() {
  const heading = screen.getByText(t('splitGroups.balances'))
  const card = heading.closest('section, div.rounded-xl, [class*="card"]') ?? document.body
  return within(card as HTMLElement)
}

function kpi(label: string) {
  return screen.getByText(label).parentElement!.parentElement!
}

describe('GroupDetailPage balances', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.settlements.mockResolvedValue([])
    api.transactions.mockResolvedValue([])
  })

  it('shows a linked member what they owe the owner, not the other way round', async () => {
    auth.userId = 'user-bob'
    api.get.mockResolvedValue(group('bob'))
    // Alice paid 100 split 70/30: Bob owes Alice 30.
    api.balances.mockResolvedValue({
      group_id: 'g1', self_member_id: 'm-bob', default_currency: 'USD', lines: [line('m-alice', -30)],
    })
    const { user } = renderPage()

    await screen.findByText(t('splitGroups.youOwe', { name: 'Alice' }))
    expect(screen.queryByText(t('splitGroups.owesYou', { name: 'Alice' }))).not.toBeInTheDocument()
    expect(kpi(t('splitGroups.kpiYouOwe'))).toHaveTextContent('$30.00')
    expect(kpi(t('splitGroups.kpiOwedToYouAsMember'))).toHaveTextContent('$0.00')
    // Bob is "(you)" and Alice is marked as the owner.
    expect(screen.getByText('Bob').parentElement).toHaveTextContent(t('splitGroups.you'))
    expect(screen.getByText('Alice').parentElement).toHaveTextContent(t('splitGroups.ownerBadge'))

    // Paying records a settlement from Bob to Alice.
    await user.click(balanceRows().getByRole('button', { name: t('splitGroups.payNow') }))
    const dialog = await screen.findByRole('dialog')
    const [from, to] = within(dialog).getAllByRole('combobox')
    expect(from).toHaveValue('m-bob')
    expect(to).toHaveValue('m-alice')
  })

  it('shows a linked member who owes them, without a pay button they cannot use', async () => {
    auth.userId = 'user-bob'
    api.get.mockResolvedValue(group('bob'))
    api.balances.mockResolvedValue({
      group_id: 'g1', self_member_id: 'm-bob', default_currency: 'USD', lines: [line('m-alice', 20)],
    })
    renderPage()

    await screen.findByText(t('splitGroups.owesYou', { name: 'Alice' }))
    expect(kpi(t('splitGroups.kpiOwedToYouAsMember'))).toHaveTextContent('$20.00')
    expect(kpi(t('splitGroups.kpiYouOwe'))).toHaveTextContent('$0.00')
    expect(screen.queryByRole('button', { name: t('splitGroups.payNow') })).not.toBeInTheDocument()
  })

  it('keeps the owner view: each line is that member against the owner', async () => {
    auth.userId = 'user-alice'
    api.get.mockResolvedValue(group('alice'))
    api.balances.mockResolvedValue({
      group_id: 'g1', self_member_id: 'm-alice', default_currency: 'USD',
      lines: [line('m-bob', 30), line('m-carol', -10)],
    })
    const { user } = renderPage()

    await screen.findByText(t('splitGroups.owesYou', { name: 'Bob' }))
    expect(screen.getByText(t('splitGroups.youOwe', { name: 'Carol' }))).toBeInTheDocument()
    expect(kpi(t('splitGroups.kpiOwedToYou'))).toHaveTextContent('$30.00')
    expect(kpi(t('splitGroups.kpiYouOwe'))).toHaveTextContent('$10.00')
    expect(screen.getByText('Alice').parentElement).toHaveTextContent(t('splitGroups.you'))

    const settle = balanceRows().getAllByRole('button', { name: t('splitGroups.settleUp') })
    expect(settle).toHaveLength(2)
    await user.click(settle[1])
    const dialog = await screen.findByRole('dialog')
    const [from, to] = within(dialog).getAllByRole('combobox')
    expect(from).toHaveValue('m-alice')
    expect(to).toHaveValue('m-carol')
  })
})
