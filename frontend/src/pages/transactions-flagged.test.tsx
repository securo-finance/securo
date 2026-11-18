/**
 * Flagged transactions (#56): a flagged row shows its marker, and the
 * "flagged only" filter read from the URL reaches the list request.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { renderWithProviders } from '@/test/utils'
import type { Transaction } from '@/types'

const api = vi.hoisted(() => ({
  transactions: { list: vi.fn(), update: vi.fn(), delete: vi.fn(), create: vi.fn() },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: {
    get: vi.fn(), list: vi.fn(), bills: vi.fn(), summary: vi.fn(),
    update: vi.fn(), runningBalance: vi.fn(),
  },
  recurring: { list: vi.fn() },
  payees: { list: vi.fn() },
  admin: { accountingMode: vi.fn() },
  groups: { get: vi.fn(), list: vi.fn() },
  rules: { create: vi.fn() },
  reconciliation: { suggestions: vi.fn() },
  currencies: { list: vi.fn() },
  settings: { attachments: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  transactions: api.transactions,
  categories: api.categories,
  categoryGroups: api.categoryGroups,
  accounts: api.accounts,
  recurring: api.recurring,
  payees: api.payees,
  admin: api.admin,
  groups: api.groups,
  rules: api.rules,
  reconciliation: api.reconciliation,
  currencies: api.currencies,
  settings: api.settings,
}))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '***' }),
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { id: 'user-1', preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/contexts/workspace-context', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/contexts/workspace-context')>()
  return { ...actual, useWorkspace: () => ({ canWrite: true, hasModule: () => true }) }
})
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({
    collections: [], activeCollectionId: null, activeCollection: null,
    setActiveCollectionId: () => {}, activeAccountIds: null, activeWalletIds: null,
  }),
}))

function row(id: string, description: string, isFlagged: boolean): Transaction {
  return {
    id, user_id: 'user-1', account_id: 'acc-1', description,
    category_id: null, category: null,
    external_id: null, original_description: null, amount: 42, currency: 'BRL',
    date: '2026-10-08', type: 'debit', source: 'manual', status: 'posted',
    payee: null, payee_id: null, payee_name: null, notes: null,
    transfer_pair_id: null, transfer_counterpart: null,
    amount_primary: null, fx_rate_used: null, fx_fallback: false,
    installment_number: null, total_installments: null, installment_total_amount: null,
    installment_purchase_date: null, installment_series_id: null,
    bill_id: null, effective_bill_date: null, reporting_date_override: null,
    splits: [], is_ignored: false, exclude_from_pnl: false, is_flagged: isFlagged,
  } as Transaction
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-10-20T12:00:00'))
  // Answer like the server: under flagged=true only the flagged row comes back.
  api.transactions.list.mockImplementation(async (params: { flagged?: boolean }) => {
    const items = [row('tx-flagged', 'Pharmacy receipt', true), row('tx-plain', 'Bakery', false)]
      .filter((tx) => !params.flagged || tx.is_flagged)
    return { items, total: items.length, page: 1, limit: 50 }
  })
  api.categories.list.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([])
  api.recurring.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
  api.admin.accountingMode.mockResolvedValue(false)
  api.groups.list.mockResolvedValue([])
  api.reconciliation.suggestions.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([])
  api.settings.attachments.mockResolvedValue(false)
})

describe('flagged transactions', () => {
  it('marks only the flagged row', async () => {
    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    const flagged = await screen.findByText('Pharmacy receipt')
    const plain = await screen.findByText('Bakery')
    expect(flagged.parentElement!.querySelector('[aria-label="Flagged"]')).not.toBeNull()
    expect(plain.parentElement!.querySelector('[aria-label="Flagged"]')).toBeNull()
  })

  it('shows only flagged rows when the URL carries flagged=1', async () => {
    renderWithProviders(<TransactionsPage />, { route: '/transactions?flagged=1', path: '/transactions' })

    expect(await screen.findByText('Pharmacy receipt')).toBeInTheDocument()
    expect(screen.queryByText('Bakery')).not.toBeInTheDocument()
    for (const [params] of api.transactions.list.mock.calls) {
      expect(params).toMatchObject({ flagged: true })
    }
  })

  it('drops a row that leaves the list from the selection', async () => {
    // Bakery was still flagged when the page loaded.
    api.transactions.list.mockResolvedValue({
      items: [row('tx-flagged', 'Pharmacy receipt', true), row('tx-plain', 'Bakery', true)],
      total: 2, page: 1, limit: 50,
    })
    const { queryClient, user } = renderWithProviders(<TransactionsPage />, {
      route: '/transactions?flagged=1', path: '/transactions',
    })

    const bakery = await screen.findByText('Bakery')
    await user.click(bakery.closest('tr')!.querySelector('input[type="checkbox"]')!)
    expect(await screen.findByText('Export Selected (1)')).toBeInTheDocument()

    // Unflagged elsewhere: the next fetch under "Flagged only" no longer has it.
    api.transactions.list.mockResolvedValue({
      items: [row('tx-flagged', 'Pharmacy receipt', true)], total: 1, page: 1, limit: 50,
    })
    await queryClient.invalidateQueries({ queryKey: ['transactions'] })

    await waitFor(() => expect(screen.queryByText('Bakery')).not.toBeInTheDocument())
    expect(screen.queryByText(/Export Selected/)).not.toBeInTheDocument()
  })

  it('shows every row and leaves the flag out of the request when the filter is off', async () => {
    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    expect(await screen.findByText('Bakery')).toBeInTheDocument()
    expect(screen.getByText('Pharmacy receipt')).toBeInTheDocument()
    for (const [params] of api.transactions.list.mock.calls) {
      expect(params.flagged).toBeUndefined()
    }
  })
})
