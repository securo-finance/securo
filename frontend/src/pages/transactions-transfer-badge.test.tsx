/**
 * The transfer badge in the transactions list names the linked account
 * (#810).
 *
 * The list marked every transfer row with a bare "Transfer" badge, so a
 * page full of transfers said nothing about what each row is connected
 * to. The list API now fills `transfer_counterpart` on each transfer
 * row, and the badge adds the other leg's account, as a link to it.
 * Rows whose counterpart the server could not resolve keep the plain badge.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { MobileTransactionRow } from '@/components/mobile-transaction-row'
import { renderWithProviders } from '@/test/utils'
import type { Account, Transaction } from '@/types'

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

const TOOLTIP = 'Transfers are excluded from report and dashboard totals'

function tx(over: Partial<Transaction> & { id: string; description: string }): Transaction {
  return {
    user_id: 'user-1', account_id: 'acc-1', category_id: null, category: null,
    external_id: null, original_description: null, amount: 1, currency: 'BRL',
    date: '2026-10-08', type: 'debit', source: 'manual', status: 'posted',
    payee: null, payee_id: null, payee_name: null, notes: null,
    transfer_pair_id: null, transfer_counterpart: null, amount_primary: null,
    fx_rate_used: null, fx_fallback: false, installment_number: null,
    total_installments: null, installment_total_amount: null,
    installment_purchase_date: null, installment_series_id: null, bill_id: null,
    effective_bill_date: null, splits: [], is_ignored: false,
    ...over,
  }
}

const pair = 'pair-1'
const rows: Transaction[] = [
  tx({
    id: 'tx-out', description: 'Transfer out', transfer_pair_id: pair,
    transfer_counterpart: { account_id: 'acc-2', account_name: 'Savings' },
  }),
  tx({
    id: 'tx-in', description: 'Transfer in', type: 'credit',
    transfer_pair_id: pair,
    transfer_counterpart: { account_id: 'acc-1', account_name: 'Checking' },
  }),
  // The server could not resolve this leg's counterpart: old badge stays.
  tx({ id: 'tx-plain', description: 'Transfer plain', transfer_pair_id: 'pair-2' }),
  tx({ id: 'tx-groceries', description: 'GROCERIES' }),
]

async function renderPage() {
  return renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-10-20T12:00:00'))
  api.transactions.list.mockResolvedValue({ items: rows, total: rows.length, page: 1, limit: 50 })
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

describe('transfer rows in the transactions list', () => {
  it('names the account each row is linked to', async () => {
    await renderPage()

    const outLink = await screen.findByRole('link', { name: 'Savings' })
    expect(outLink).toHaveAttribute('href', '/accounts/acc-2')
    const inLink = await screen.findByRole('link', { name: 'Checking' })
    expect(inLink).toHaveAttribute('href', '/accounts/acc-1')
    // The name sits inside the existing "Transfer" badge, which keeps its
    // label and its explanation tooltip.
    const outBadge = outLink.parentElement!
    expect(outBadge).toHaveTextContent('Transfer')
    expect(outBadge.querySelector(`[title="${TOOLTIP}"]`)).not.toBeNull()

    // A row whose counterpart the server could not resolve keeps the plain
    // badge: the "Transfer" word, no name, no link to follow.
    const plainRow = (await screen.findByText('Transfer plain')).closest('div[class*="flex"]')!
    expect(plainRow).toHaveTextContent('Transfer')
    expect(plainRow.querySelector('a[href^="/accounts/"]')).toBeNull()
  })

  it('opens the linked account from the badge', async () => {
    const { user } = await renderPage()

    await user.click(await screen.findByRole('link', { name: 'Savings' }))

    // The click navigates to /accounts/acc-2; this test mounts the page
    // only under /transactions, so the list unmounts with the route.
    await waitFor(() => expect(screen.queryByText('Transfer out')).not.toBeInTheDocument())
  })

  it('names the linked account next to the account on the mobile row', () => {
    const checking = { id: 'acc-1', name: 'Checking', display_name: null, type: 'checking' } as unknown as Account
    renderWithProviders(
      <MobileTransactionRow
        tx={rows[0]}
        account={checking}
        groupName={undefined}
        selected={false}
        selectable={false}
        canWrite={true}
        highlighted={false}
        locale="en-US"
        userCurrency="BRL"
        onSelect={() => {}}
        onClick={() => {}}
      />,
    )

    // rows[0] is linked to 'Savings': the name is visible text on the row,
    // so it works on touch, not only as a hover tooltip.
    expect(screen.getByText('Checking')).toBeInTheDocument()
    expect(screen.getByText('Savings')).toBeInTheDocument()
  })
})
