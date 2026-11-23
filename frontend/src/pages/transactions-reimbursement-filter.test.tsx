/**
 * Filtering by reimbursement link (#1126): the URL value reaches the list
 * request, the filter menu sets it and shows a chip, and the chip clears it.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { renderWithProviders, t } from '@/test/utils'
import type { Transaction } from '@/types'

const api = vi.hoisted(() => ({
  transactions: { list: vi.fn(), update: vi.fn(), delete: vi.fn(), create: vi.fn(), bulkFlag: vi.fn() },
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

function row(id: string, description: string, role: 'anchor' | 'counterpart' | null): Transaction {
  return {
    id, user_id: 'user-1', account_id: 'acc-1', description,
    category_id: null, category: null,
    external_id: null, original_description: null, amount: 42, currency: 'BRL',
    date: '2026-10-08', type: role === 'counterpart' ? 'credit' : 'debit', source: 'manual', status: 'posted',
    payee: null, payee_id: null, payee_name: null, notes: null,
    transfer_pair_id: null, transfer_counterpart: null,
    amount_primary: null, fx_rate_used: null, fx_fallback: false,
    installment_number: null, total_installments: null, installment_total_amount: null,
    installment_purchase_date: null, installment_series_id: null,
    bill_id: null, effective_bill_date: null, reporting_date_override: null,
    splits: [], is_ignored: false, exclude_from_pnl: false, is_flagged: false,
    reimbursement_role: role,
  } as Transaction
}

const ROWS = [
  row('tx-anchor', 'Dinner for four', 'anchor'),
  row('tx-counterpart', 'Alice pays back', 'counterpart'),
  row('tx-plain', 'Bakery', null),
]

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-10-20T12:00:00'))
  // Answer like the server: each value keeps the rows it names.
  api.transactions.list.mockImplementation(async (params: { reimbursement?: string }) => {
    const keep: Record<string, (tx: Transaction) => boolean> = {
      linked: (tx) => tx.reimbursement_role != null,
      anchor: (tx) => tx.reimbursement_role === 'anchor',
      counterpart: (tx) => tx.reimbursement_role === 'counterpart',
      unlinked: (tx) => tx.reimbursement_role == null,
    }
    const items = ROWS.filter(params.reimbursement ? keep[params.reimbursement] : () => true)
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

describe('reimbursement filter', () => {
  it('reads the filter from the URL and asks the server for it', async () => {
    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?reimbursement=counterpart', path: '/transactions',
    })

    expect(await screen.findByText('Alice pays back')).toBeInTheDocument()
    expect(screen.queryByText('Dinner for four')).not.toBeInTheDocument()
    expect(screen.queryByText('Bakery')).not.toBeInTheDocument()
    for (const [params] of api.transactions.list.mock.calls) {
      expect(params).toMatchObject({ reimbursement: 'counterpart' })
    }
  })

  it('ignores a value it does not know', async () => {
    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?reimbursement=nonsense', path: '/transactions',
    })

    expect(await screen.findByText('Bakery')).toBeInTheDocument()
    for (const [params] of api.transactions.list.mock.calls) {
      expect(params.reimbursement).toBeUndefined()
    }
  })

  it('still shows its chip when it is the only filter left', async () => {
    // The chip row used to open only for the other filters, so with no date
    // range the active reimbursement filter could be neither seen nor removed.
    const { user } = renderWithProviders(<TransactionsPage />, {
      route: '/transactions?reimbursement=counterpart', path: '/transactions',
    })
    await screen.findByText('Alice pays back')

    await user.click(screen.getByRole('button', { name: new RegExp(`^${t('transactions.filtersBar.date')}:`) }))

    expect(await screen.findByRole('button', {
      name: new RegExp(`${t('transactions.reimbursementFilter')}:\\s*${t('transactions.reimbursementCounterpart')}`),
    })).toBeInTheDocument()
  })

  it('is picked from the filter menu, shows as a chip, and the chip clears it', async () => {
    const { user } = renderWithProviders(<TransactionsPage />, {
      route: '/transactions', path: '/transactions',
    })
    await screen.findByText('Bakery')

    await user.click(screen.getByRole('button', { name: t('transactions.filtersBar.filters') }))
    // The dropdown holds the phone list and the desktop submenus (CSS shows one
    // per breakpoint); the submenu trigger is the one that can open.
    const triggers = await screen.findAllByRole('menuitem', { name: t('transactions.reimbursementFilter') })
    await user.click(triggers.find((item) => item.getAttribute('data-state') === 'closed')!)
    const option = await screen.findByRole('menuitem', { name: t('transactions.reimbursementUnlinked') })
    option.focus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(api.transactions.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ reimbursement: 'unlinked' }),
    ))
    expect(await screen.findByText('Bakery')).toBeInTheDocument()
    expect(screen.queryByText('Dinner for four')).not.toBeInTheDocument()

    // The chip is a button that removes its own filter.
    await user.click(screen.getByRole('button', {
      name: new RegExp(`${t('transactions.reimbursementFilter')}:\\s*${t('transactions.reimbursementUnlinked')}`),
    }))

    await waitFor(() => expect(api.transactions.list.mock.lastCall?.[0].reimbursement).toBeUndefined())
    expect(await screen.findByText('Dinner for four')).toBeInTheDocument()
  })
})
