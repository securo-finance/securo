/**
 * The description cell keeps its badges inside its own column (#1306).
 *
 * The Description column has a fixed width. A row that carries several
 * badges at once (a transfer with its counterpart account, the report
 * exclusion marker, an installment counter) needed more room than the
 * column has, and because the badges sat on a single non-wrapping line
 * they ran past the cell and painted over the Category column next to
 * it. The description itself was squeezed to nothing in the process.
 *
 * jsdom has no layout, so the guarantee is checked structurally: the
 * line wraps, and every badge on it is either small enough to never
 * need shrinking (`shrink-0`) or capped at the cell width so its own
 * text truncates (`max-w-full`). Either way nothing can push the line
 * wider than the column.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

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

// The row from the report: a transfer-categorised purchase split into 18
// installments and kept out of the reports, so three badges land on the
// same line as the description.
const crowded: Transaction = {
  id: 'tx-crowded', user_id: 'user-1', account_id: 'acc-1',
  // Deliberately free of the badge words: an assertion on the badges below
  // would otherwise pass on the description's own text.
  description: 'Payment to Vinicius in 18 parts',
  category_id: 'cat-1',
  category: {
    id: 'cat-1', name: 'Transfers', icon: 'arrow-left-right',
    color: '#6B7280', treat_as_transfer: true,
  } as Transaction['category'],
  external_id: null, original_description: null, amount: 180, currency: 'BRL',
  date: '2026-10-08', type: 'debit', source: 'manual', status: 'posted',
  payee: null, payee_id: null, payee_name: null, notes: null,
  transfer_pair_id: null,
  transfer_counterpart: null,
  amount_primary: null, fx_rate_used: null, fx_fallback: false,
  installment_number: 18, total_installments: 18, installment_total_amount: 3240,
  installment_purchase_date: '2025-05-08', installment_series_id: 'series-1',
  bill_id: null, effective_bill_date: null, splits: [], is_ignored: false,
  exclude_from_pnl: true,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-10-20T12:00:00'))
  api.transactions.list.mockResolvedValue({ items: [crowded], total: 1, page: 1, limit: 50 })
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

describe('the description cell on a row with several badges', () => {
  it('wraps the badges instead of letting them run into the next column', async () => {
    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })

    const description = await screen.findByText('Payment to Vinicius in 18 parts')
    const line = description.parentElement!

    // All three badges share the line with the description. Each one is
    // looked up as its own element, so dropping a badge fails the test
    // instead of being covered by the text of its neighbours.
    const badges = Array.from(line.children).filter((child) => child !== description)
    const labels = badges.map((badge) => badge.textContent?.trim())
    expect(labels).toContain('Transfer')
    expect(labels).toContain('Not in reports')
    expect(labels).toContain('18/18')

    expect(line.className).toContain('flex-wrap')

    // The description truncates rather than widening the line, and every
    // badge beside it is either unshrinkable-but-small or capped at the
    // cell width. Nothing here can overflow the column.
    expect(description.className).toContain('truncate')
    expect(description.className).toContain('max-w-full')
    for (const badge of Array.from(line.children)) {
      if (badge === description) continue
      expect(
        badge.className.includes('max-w-full') || badge.className.includes('shrink-0'),
        `badge "${badge.textContent}" can push the description line past the column`,
      ).toBe(true)
    }
  })
})
