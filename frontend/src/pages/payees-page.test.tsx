import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import PayeesPage from '@/pages/payees'
import { PAYEE_SORT_STORAGE_KEY } from '@/lib/payee-sorting'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  payees: {
    list: vi.fn(),
    summary: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    merge: vi.fn(),
    bulkDelete: vi.fn(),
  },
  transactions: { list: vi.fn() },
  fiscal: { taxIdKinds: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  payees: api.payees,
  transactions: api.transactions,
  fiscal: api.fiscal,
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: false }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

const payees = [
  {
    id: 'alpha', user_id: 'user-1', name: 'Alpha', type: 'person', source: 'manual', is_favorite: false,
    notes: null, email: null, phone: null, address: null, website: null, tax_ids: [],
    created_at: '2026-01-01T00:00:00Z', transaction_count: 1,
  },
  {
    id: 'zeta', user_id: 'user-1', name: 'Zeta', type: 'company', source: 'manual', is_favorite: false,
    notes: null, email: null, phone: null, address: null, website: null, tax_ids: [],
    created_at: '2026-01-01T00:00:00Z', transaction_count: 3,
  },
]

function firstPayeeRow() {
  return screen.getAllByRole('row')[1]
}

describe('PayeesPage sorting', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.payees.list.mockResolvedValue(payees)
    api.fiscal.taxIdKinds.mockResolvedValue({ kinds: [], jurisdiction: null })
  })

  it('sorts transactions descending then ascending and persists the choice', async () => {
    const { user } = renderWithProviders(<PayeesPage />, { route: '/payees' })
    await screen.findByText('Alpha')

    const transactionsHeader = screen.getByRole('button', { name: t('payees.transactionCount') })
    await user.click(transactionsHeader)

    expect(within(firstPayeeRow()).getByText('Zeta')).toBeInTheDocument()
    expect(transactionsHeader).toHaveAccessibleName(t('payees.transactionCount'))
    expect(transactionsHeader.closest('th')).toHaveAttribute('aria-sort', 'descending')
    await waitFor(() => expect(localStorage.getItem(PAYEE_SORT_STORAGE_KEY)).toBe(
      JSON.stringify({ by: 'transaction_count', direction: 'desc' }),
    ))

    await user.click(transactionsHeader)

    expect(within(firstPayeeRow()).getByText('Alpha')).toBeInTheDocument()
    expect(transactionsHeader.closest('th')).toHaveAttribute('aria-sort', 'ascending')
    await waitFor(() => expect(localStorage.getItem(PAYEE_SORT_STORAGE_KEY)).toBe(
      JSON.stringify({ by: 'transaction_count', direction: 'asc' }),
    ))
  })
})
