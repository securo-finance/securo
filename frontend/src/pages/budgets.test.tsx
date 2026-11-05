import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { format } from 'date-fns'
import { monthRange } from '@/lib/month-utils'

import BudgetsPage from '@/pages/budgets'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  activeAccountIds: null as string[] | null,
  mask: vi.fn((value: string) => value),
  dashboard: { spendingByCategory: vi.fn(), projectedTransactions: vi.fn() },
  transactions: { list: vi.fn() },
  admin: { accountingMode: vi.fn() },
  budgets: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    comparison: vi.fn(),
    suggestions: vi.fn(),
    createBatch: vi.fn(),
  },
  categories: {
    list: vi.fn(),
    listIncludingHidden: vi.fn(),
  },
  categoryGroups: {
    list: vi.fn(),
  },
}))

vi.mock('@/lib/api', () => ({
  dashboard: api.dashboard,
  transactions: api.transactions,
  admin: api.admin,
  budgets: api.budgets,
  categories: api.categories,
  categoryGroups: api.categoryGroups,
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: api.activeAccountIds }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: api.mask }),
}))

const categories = [
  {
    id: 'cat-groceries', user_id: 'user-1', group_id: null, name: 'Groceries',
    icon: '', color: '#34d399', is_system: false, is_hidden: false,
    treat_as_transfer: false, is_ignored: false,
  },
  {
    id: 'cat-utilities', user_id: 'user-1', group_id: null, name: 'Utilities',
    icon: '', color: '#f59e0b', is_system: false, is_hidden: false,
    treat_as_transfer: false, is_ignored: false,
  },
]

function suggestion(partial: Record<string, unknown>) {
  return {
    category_id: 'cat-groceries',
    category_name: 'Groceries',
    category_icon: '',
    category_color: '#34d399',
    group_id: null,
    group_name: null,
    suggested_amount: '120.50',
    total_spent: '361.50',
    months_with_data: 3,
    existing_budget: false,
    ...partial,
  }
}

function mockSuggestions(suggestions: unknown[]) {
  api.budgets.suggestions.mockResolvedValue({
    month: '2026-10-01', months_requested: 3, months_analyzed: 3, suggestions,
  })
}

const expectedMonth = `${format(new Date(), 'yyyy-MM')}-01`

describe('BudgetsPage suggestions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.activeAccountIds = null
    api.mask.mockImplementation(value => value)
    api.dashboard.spendingByCategory.mockResolvedValue([])
    api.dashboard.projectedTransactions.mockResolvedValue([])
    api.transactions.list.mockResolvedValue({ items: [], total: 0 })
    api.admin.accountingMode.mockResolvedValue({ mode: 'cash' })
    api.budgets.list.mockResolvedValue([])
    api.categories.list.mockResolvedValue(categories)
    api.categories.listIncludingHidden.mockResolvedValue(categories)
    api.categoryGroups.list.mockResolvedValue([])
  })


  function mockBudgets() {
    api.budgets.list.mockResolvedValue(categories.map((category, index) => ({
      id: `budget-${index}`, category_id: category.id, amount: 500,
      month: expectedMonth, is_recurring: false,
    })))
  }

  it('shows Overview projected spending and retains zero-activity budget rows', async () => {
    mockBudgets()
    api.dashboard.spendingByCategory.mockResolvedValue([
      { category_id: 'cat-groceries', total: 20, projected_total: 123.45 },
      { category_id: 'unbudgeted', total: 99, projected_total: 99 },
    ])
    renderWithProviders(<BudgetsPage />)
    expect(await screen.findByText('$123.45')).toBeInTheDocument()
    expect(screen.getByText('$0.00')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(3)
    expect(screen.getByText(t('budgets.spendingDescription'))).toBeInTheDocument()
    expect(api.dashboard.spendingByCategory).toHaveBeenCalledWith(expectedMonth, undefined)
  })

  it.each([
    [0, 0, 'bg-emerald-500'],
    [250, 50, 'bg-emerald-500'],
    [400, 80, 'bg-amber-400'],
    [500, 100, 'bg-amber-400'],
    [750, 100, 'bg-rose-500'],
  ])('renders Overview progress for spending %s', async (amount, width, color) => {
    mockBudgets()
    api.dashboard.spendingByCategory.mockResolvedValue([
      { category_id: 'cat-groceries', total: 0, projected_total: amount },
    ])
    renderWithProviders(<BudgetsPage />)
    const row = (await screen.findByText('Groceries')).closest('tr')!
    await waitFor(() => expect(within(row).getByRole('progressbar')).toHaveAttribute('aria-valuenow', String(width)))
    const bar = within(row).getByRole('progressbar')
    expect(bar.firstElementChild).toHaveStyle({ width: `${width}%` })
    expect(bar.firstElementChild).toHaveClass(color)
    expect(within(row).getByText(t('dashboard.ofBudget', { budget: '$500.00' }))).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader').map(header => header.textContent)).toEqual([
      t('budgets.category'), t('budgets.spending'), t('budgets.amount'), t('budgets.actions'),
    ])
  })

  it('omits the progress bar for a zero limit, as Overview does', async () => {
    api.budgets.list.mockResolvedValue([{ id: 'zero', category_id: 'cat-groceries', amount: 0, month: expectedMonth }])
    api.dashboard.spendingByCategory.mockResolvedValue([{ category_id: 'cat-groceries', projected_total: 25 }])
    renderWithProviders(<BudgetsPage />)
    expect(await screen.findByText('$25.00')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('opens the shared sidebar from a row and supports close and repeated clicks', async () => {
    mockBudgets()
    api.transactions.list.mockResolvedValue({ items: [{ id: 'tx-1', description: 'Weekly groceries', date: expectedMonth, type: 'debit', amount: 25, currency: 'USD', status: 'posted' }], total: 1 })
    const { user } = renderWithProviders(<BudgetsPage />)
    const row = (await screen.findByText('Groceries')).closest('tr')!
    await user.click(within(row).getByRole('progressbar'))
    expect(await screen.findByText('Weekly groceries')).toBeInTheDocument()
    expect(api.transactions.list).toHaveBeenCalledWith(expect.objectContaining({ category_id: 'cat-groceries', type: 'debit', ...monthRange(expectedMonth.slice(0, 7)), account_ids: undefined, user_pnl_only: true }))
    await user.click(screen.getByRole('button', { name: t('common.close') }))
    expect(screen.queryByText('Weekly groceries')).not.toBeInTheDocument()
    expect(within(row).getByRole('button', { name: /Groceries/ })).toHaveFocus()
    const button = within(row).getByRole('button', { name: /Groceries/ })
    button.focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByText('Weekly groceries')).toBeInTheDocument()
    expect(screen.getByRole('complementary')).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByText('Weekly groceries')).not.toBeInTheDocument()
    expect(button).toHaveFocus()
    await user.click(button)
    expect(await screen.findByText('Weekly groceries')).toBeInTheDocument()
  })

  it('opens empty categories and keeps the sidebar in sync with month and account filters', async () => {
    mockBudgets()
    api.activeAccountIds = ['account-1']
    const { user, rerender } = renderWithProviders(<BudgetsPage />)
    await user.click(await screen.findByRole('button', { name: /Utilities/ }))
    expect(await screen.findByText(t('dashboard.drillDownEmpty'))).toBeInTheDocument()
    expect(api.transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({ category_id: 'cat-utilities', account_ids: ['account-1'], ...monthRange(expectedMonth.slice(0,7)) }))
    api.activeAccountIds = ['account-2']
    rerender(<BudgetsPage />)
    await waitFor(() => expect(api.transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({ account_ids: ['account-2'] })))
    screen.getByRole('button', { name: '‹' }).focus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('complementary')).toBeInTheDocument()
    const previous = format(new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1), 'yyyy-MM')
    await waitFor(() => expect(api.transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({ ...monthRange(previous) })))
  })

  it('shows an empty sidebar without fetching all accounts for a wallet-only collection', async () => {
    mockBudgets()
    api.activeAccountIds = []
    const { user } = renderWithProviders(<BudgetsPage />)
    await user.click(await screen.findByRole('button', { name: /Groceries/ }))
    expect(await screen.findByText(t('dashboard.drillDownEmpty'))).toBeInTheDocument()
    expect(api.transactions.list).not.toHaveBeenCalled()
    expect(api.dashboard.projectedTransactions).not.toHaveBeenCalled()
  })

  it('filters projected rows by account, category, debit type, and selected month', async () => {
    mockBudgets()
    api.activeAccountIds = ['account-1']
    const projection = { recurring_id: 'r1', account_id: 'account-1', category_id: 'cat-groceries', type: 'debit', date: expectedMonth, amount: 30, currency: 'USD', description: 'Matching forecast' }
    api.dashboard.projectedTransactions.mockResolvedValue([
      projection,
      { ...projection, recurring_id: 'r2', account_id: 'other', description: 'Other account' },
      { ...projection, recurring_id: 'r3', category_id: 'cat-utilities', description: 'Other category' },
      { ...projection, recurring_id: 'r4', type: 'credit', description: 'Income forecast' },
      { ...projection, recurring_id: 'r5', date: '2000-01-01', description: 'Old forecast' },
    ])
    const { user } = renderWithProviders(<BudgetsPage />)
    await user.click(await screen.findByRole('button', { name: /Groceries/ }))
    expect(await screen.findByText('Matching forecast')).toBeInTheDocument()
    for (const description of ['Other account', 'Other category', 'Income forecast', 'Old forecast']) {
      expect(screen.queryByText(description)).not.toBeInTheDocument()
    }
    expect(api.dashboard.projectedTransactions).toHaveBeenCalledWith({ month: expectedMonth })
  })

  it('preserves privacy masking for sidebar transaction amounts', async () => {
    mockBudgets()
    api.mask.mockImplementation(() => '••••')
    api.transactions.list.mockResolvedValue({ items: [{ id: 'tx-1', description: 'Private groceries', date: expectedMonth, type: 'debit', amount: 123.45, currency: 'USD', status: 'posted' }], total: 1 })
    const { user } = renderWithProviders(<BudgetsPage />)
    await user.click(await screen.findByRole('button', { name: /Groceries/ }))
    expect(await screen.findByText('Private groceries')).toBeInTheDocument()
    expect(screen.queryByText(/123.45/)).not.toBeInTheDocument()
    expect(api.mask).toHaveBeenCalledWith('$123.45')
  })

  it.each([null, ['account-1']])('uses split shares only for the unfiltered spending scope (%s)', async (accountIds) => {
    mockBudgets()
    api.activeAccountIds = accountIds
    api.transactions.list.mockResolvedValue({ items: [{ id: 'split', description: 'Split groceries', date: expectedMonth, type: 'debit', amount: 120, amount_primary: 240, viewer_share: 30, currency: 'EUR', status: 'posted' }], total: 1 })
    const { user } = renderWithProviders(<BudgetsPage />)
    await user.click(await screen.findByRole('button', { name: /Groceries/ }))
    const panel = screen.getByRole('complementary')
    expect(await within(panel).findByText('Split groceries')).toBeInTheDocument()
    expect(within(panel).getByText(accountIds ? '-€120.00' : '-€30.00')).toBeInTheDocument()
    expect(within(panel).getAllByText(accountIds ? '$240.00' : '$60.00').length).toBeGreaterThan(0)
  })

  it.each(['transactions', 'projections', 'later-page'])('shows a load error rather than incomplete totals when %s fails', async (failure) => {
    mockBudgets()
    if (failure === 'projections') api.dashboard.projectedTransactions.mockRejectedValue(new Error('unavailable'))
    else if (failure === 'later-page') api.transactions.list
      .mockResolvedValueOnce({ items: [{ id: 'partial', description: 'Partial result', amount: 10, date: expectedMonth, currency: 'USD', type: 'debit' }], total: 2 })
      .mockRejectedValue(new Error('unavailable'))
    else api.transactions.list.mockRejectedValue(new Error('unavailable'))
    const { user } = renderWithProviders(<BudgetsPage />)
    await user.click(await screen.findByRole('button', { name: /Groceries/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(t('dashboard.drillDownError'))
    expect(screen.queryByText(t('dashboard.drillDownEmpty'))).not.toBeInTheDocument()
    expect(screen.queryByText('Partial result')).not.toBeInTheDocument()
    api.transactions.list.mockResolvedValue({ items: [], total: 0 })
    api.dashboard.projectedTransactions.mockResolvedValue([])
    await user.click(screen.getByRole('button', { name: t('common.retry') }))
    expect(await screen.findByText(t('dashboard.drillDownEmpty'))).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('ignores a cached projection error when switching to a wallet-only collection', async () => {
    mockBudgets()
    api.dashboard.projectedTransactions.mockRejectedValue(new Error('unavailable'))
    const { user, rerender } = renderWithProviders(<BudgetsPage />)
    await user.click(await screen.findByRole('button', { name: /Groceries/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent(t('dashboard.drillDownError'))
    const transactionRequests = api.transactions.list.mock.calls.length
    api.activeAccountIds = []
    rerender(<BudgetsPage />)
    expect(await screen.findByText(t('dashboard.drillDownEmpty'))).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(api.transactions.list).toHaveBeenCalledTimes(transactionRequests)
  })

  it('loads every transaction page and shows the complete total with the same filter', async () => {
    mockBudgets()
    const rows = Array.from({ length: 201 }, (_, index) => ({ id: `tx-${index}`, description: `Grocery ${index}`, date: expectedMonth, type: 'debit', amount: 1, currency: 'USD', status: 'posted' }))
    api.transactions.list.mockImplementation(async ({ page = 1, limit = 200 }) => ({ items: rows.slice((page - 1) * limit, page * limit), total: rows.length }))
    const { user } = renderWithProviders(<BudgetsPage />)
    await user.click(await screen.findByRole('button', { name: /Groceries/ }))
    expect(await screen.findByText('Grocery 200')).toBeInTheDocument()
    expect(within(screen.getByRole('complementary')).getAllByText('$201.00').length).toBeGreaterThan(0)
    expect(api.transactions.list).toHaveBeenCalledTimes(2)
    expect(api.transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, category_id: 'cat-groceries', ...monthRange(expectedMonth.slice(0,7)) }))
    expect(screen.getByText('Grocery 0').closest('.cursor-pointer')).toBeNull()
  })

  it('keeps edit and delete actions separate from the transaction sidebar', async () => {
    mockBudgets()
    const { user } = renderWithProviders(<BudgetsPage />)
    const row = (await screen.findByText('Groceries')).closest('tr')!
    await user.click(within(row).getByRole('button', { name: t('common.edit') }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(api.transactions.list).not.toHaveBeenCalled()
    await user.keyboard('{Escape}')
    await user.click(within(row).getByRole('button', { name: t('common.delete') }))
    expect(await screen.findByRole('dialog', { name: t('budgets.confirmDeleteTitle') })).toBeInTheDocument()
    expect(api.transactions.list).not.toHaveBeenCalled()
  })

  it('uses the selected month and collection accounts', async () => {
    api.activeAccountIds = ['account-1']
    mockBudgets()
    api.dashboard.spendingByCategory.mockImplementation((month) => Promise.resolve([
      { category_id: 'cat-groceries', projected_total: month === expectedMonth ? 125 : 75 },
    ]))
    const { user } = renderWithProviders(<BudgetsPage />)
    expect(await screen.findByText('$125.00')).toBeInTheDocument()
    await waitFor(() => expect(api.dashboard.spendingByCategory).toHaveBeenCalledWith(expectedMonth, ['account-1']))
    await user.click(screen.getByRole('button', { name: '‹' }))
    expect(await screen.findByText('$75.00')).toBeInTheDocument()
    expect(screen.queryByText('$125.00')).not.toBeInTheDocument()
    const previous = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1)
    await waitFor(() => expect(api.dashboard.spendingByCategory).toHaveBeenCalledWith(`${format(previous, 'yyyy-MM')}-01`, ['account-1']))
  })

  it('refreshes spending when the active account filter changes', async () => {
    mockBudgets()
    api.dashboard.spendingByCategory.mockImplementation((_month, accountIds) => Promise.resolve([
      { category_id: 'cat-groceries', projected_total: accountIds ? 25 : 125 },
    ]))
    const { rerender } = renderWithProviders(<BudgetsPage />)
    expect(await screen.findByText('$125.00')).toBeInTheDocument()
    api.activeAccountIds = ['account-2']
    rerender(<BudgetsPage />)
    expect(await screen.findByText('$25.00')).toBeInTheDocument()
    expect(screen.queryByText('$125.00')).not.toBeInTheDocument()
    expect(api.dashboard.spendingByCategory).toHaveBeenLastCalledWith(expectedMonth, ['account-2'])
  })

  it('does not fetch all accounts for a wallet-only collection', async () => {
    api.activeAccountIds = []
    mockBudgets()
    renderWithProviders(<BudgetsPage />)
    expect(await screen.findAllByText('$0.00')).toHaveLength(2)
    expect(api.dashboard.spendingByCategory).not.toHaveBeenCalled()
    expect(screen.getAllByRole('progressbar')).toHaveLength(2)
    for (const bar of screen.getAllByRole('progressbar')) expect(bar).toHaveAttribute('aria-valuenow', '0')
  })

  it('masks spending with the existing privacy formatter', async () => {
    mockBudgets()
    api.dashboard.spendingByCategory.mockResolvedValue([{ category_id: 'cat-groceries', projected_total: 123.45 }])
    api.mask.mockImplementation(() => '••••')
    renderWithProviders(<BudgetsPage />)
    await waitFor(() => expect(api.mask).toHaveBeenCalledWith('$123.45'))
    expect(screen.queryByText('$123.45')).not.toBeInTheDocument()
    for (const bar of screen.getAllByRole('progressbar')) expect(bar).toHaveAttribute('aria-valuetext', '••••')
  })

  it('does not present a failed spending request as zero', async () => {
    mockBudgets()
    api.dashboard.spendingByCategory.mockRejectedValue(new Error('unavailable'))
    renderWithProviders(<BudgetsPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent(t('budgets.spendingError'))
    expect(screen.queryByText('$0.00')).not.toBeInTheDocument()
    expect(screen.getAllByText('—')).toHaveLength(2)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })

  it('offers the suggest button next to the add button', async () => {
    renderWithProviders(<BudgetsPage />, { route: '/budgets' })

    expect(await screen.findByRole('button', { name: t('budgets.suggest') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('budgets.add') })).toBeInTheDocument()
  })

  it('lists only categories without a budget and creates them in one batch', async () => {
    mockSuggestions([
      suggestion({}),
      suggestion({
        category_id: 'cat-utilities',
        category_name: 'Utilities',
        suggested_amount: '80.00',
        total_spent: '240.00',
        existing_budget: true,
      }),
    ])
    api.budgets.createBatch.mockResolvedValue([{ id: 'b1', user_id: 'user-1', category_id: 'cat-groceries', amount: 120.5, month: expectedMonth, is_recurring: false }])
    const { user } = renderWithProviders(<BudgetsPage />, { route: '/budgets' })

    await user.click(await screen.findByRole('button', { name: t('budgets.suggest') }))
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByText('Groceries')).toBeInTheDocument()
    expect(within(dialog).queryByText('Utilities')).not.toBeInTheDocument()
    expect(within(dialog).getByDisplayValue('120.50')).toBeInTheDocument()
    expect(api.budgets.suggestions).toHaveBeenCalledWith(expectedMonth, 3)

    await user.click(within(dialog).getByRole('button', { name: t('budgets.suggestCreate') }))

    await waitFor(() => {
      expect(api.budgets.createBatch).toHaveBeenCalledWith({
        month: expectedMonth,
        is_recurring: false,
        items: [{ category_id: 'cat-groceries', amount: 120.5 }],
      })
    })
    await waitFor(() => expect(api.budgets.list.mock.calls.length).toBeGreaterThanOrEqual(2))
  })

  it('shows the empty message when every category already has a budget', async () => {
    mockSuggestions([suggestion({ existing_budget: true })])
    const { user } = renderWithProviders(<BudgetsPage />, { route: '/budgets' })

    await user.click(await screen.findByRole('button', { name: t('budgets.suggest') }))
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByText(t('budgets.suggestEmpty'))).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: t('budgets.suggestCreate') })).toBeDisabled()
  })

  it('shows an error, not the empty message, when suggestions fail to load', async () => {
    api.budgets.suggestions.mockRejectedValue(new Error('server error'))
    const { user } = renderWithProviders(<BudgetsPage />, { route: '/budgets' })

    await user.click(await screen.findByRole('button', { name: t('budgets.suggest') }))
    const dialog = await screen.findByRole('dialog')

    expect(await within(dialog).findByText(t('budgets.suggestError'))).toBeInTheDocument()
    expect(within(dialog).queryByText(t('budgets.suggestEmpty'))).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: t('budgets.suggestCreate') })).toBeDisabled()
  })

  it('only sends checked rows with a positive amount', async () => {
    mockSuggestions([
      suggestion({}),
      suggestion({
        category_id: 'cat-utilities',
        category_name: 'Utilities',
        suggested_amount: '80.00',
        total_spent: '240.00',
      }),
    ])
    api.budgets.createBatch.mockResolvedValue([{ id: 'b1', user_id: 'user-1', category_id: 'cat-utilities', amount: 80, month: expectedMonth, is_recurring: false }])
    const { user } = renderWithProviders(<BudgetsPage />, { route: '/budgets' })

    await user.click(await screen.findByRole('button', { name: t('budgets.suggest') }))
    const dialog = await screen.findByRole('dialog')

    await user.click(within(dialog).getByRole('checkbox', { name: 'Groceries' }))
    await user.click(within(dialog).getByRole('button', { name: t('budgets.suggestCreate') }))

    await waitFor(() => {
      expect(api.budgets.createBatch).toHaveBeenCalledWith({
        month: expectedMonth,
        is_recurring: false,
        items: [{ category_id: 'cat-utilities', amount: 80 }],
      })
    })
  })

  it('marks the created budgets as recurring when the repeat checkbox is on', async () => {
    mockSuggestions([suggestion({})])
    api.budgets.createBatch.mockResolvedValue([{ id: 'b1', user_id: 'user-1', category_id: 'cat-groceries', amount: 120.5, month: expectedMonth, is_recurring: true }])
    const { user } = renderWithProviders(<BudgetsPage />, { route: '/budgets' })

    await user.click(await screen.findByRole('button', { name: t('budgets.suggest') }))
    const dialog = await screen.findByRole('dialog')

    await user.click(within(dialog).getByRole('checkbox', { name: t('budgets.repeatEveryMonth') }))
    await user.click(within(dialog).getByRole('button', { name: t('budgets.suggestCreate') }))

    await waitFor(() => {
      expect(api.budgets.createBatch).toHaveBeenCalledWith({
        month: expectedMonth,
        is_recurring: true,
        items: [{ category_id: 'cat-groceries', amount: 120.5 }],
      })
    })
  })
})
