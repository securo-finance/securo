/**
 * Paying a recurring bill before its due date from the recurring page.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import RecurringPage from '@/pages/recurring'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  recurring: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    generate: vi.fn(),
  },
  recurringPayments: { pay: vi.fn() },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: { list: vi.fn() },
  currencies: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

function bill(id: string, description: string, overrides = {}) {
  return {
    id,
    user_id: 'user-1',
    account_id: 'acc-1',
    category_id: null,
    description,
    amount: 120,
    currency: 'BRL',
    type: 'debit',
    frequency: 'monthly',
    weekend_adjustment: 'none',
    day_of_month: null,
    start_date: '2026-01-15',
    end_date: null,
    is_active: true,
    auto_generate: true,
    next_occurrence: '2026-09-15',
    amount_primary: null,
    fx_rate_used: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-09-08T12:00:00'))
  api.recurring.list.mockResolvedValue([
    bill('rec-1', 'Internet Fibra'),
    bill('rec-2', 'Old Gym', { is_active: false }),
  ])
  api.recurringPayments.pay.mockResolvedValue({ transaction: {}, recurring: bill('rec-1', 'Internet Fibra') })
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([
    { id: 'acc-1', name: 'Checking' },
    { id: 'acc-2', name: 'Savings' },
  ])
})

describe('pay now', () => {
  it('submits the edited payment for the bill', async () => {
    const { user } = renderWithProviders(<RecurringPage />)

    const row = (await screen.findByText('Internet Fibra')).closest('tr')!
    await user.click(within(row).getByTitle('Pay now'))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText('Description')).toHaveValue('Internet Fibra')
    expect(within(dialog).getByLabelText('Amount')).toHaveValue(120)
    await waitFor(() => expect(within(dialog).getByLabelText('Account')).toHaveValue('acc-1'))

    await user.clear(within(dialog).getByLabelText('Amount'))
    await user.type(within(dialog).getByLabelText('Amount'), '99.90')
    await user.selectOptions(within(dialog).getByLabelText('Account'), 'acc-2')
    await user.click(within(dialog).getByRole('button', { name: 'Pay now' }))

    await waitFor(() =>
      expect(api.recurringPayments.pay).toHaveBeenCalledWith('rec-1', {
        date: '2026-09-08',
        account_id: 'acc-2',
        amount: 99.9,
        description: 'Internet Fibra',
      }),
    )
  })

  it('offers no payment for an inactive bill', async () => {
    renderWithProviders(<RecurringPage />)

    const row = (await screen.findByText('Old Gym')).closest('tr')!
    expect(within(row).queryByTitle('Pay now')).toBeNull()
  })
})
