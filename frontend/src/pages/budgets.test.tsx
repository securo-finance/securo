import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor, within } from '@testing-library/react'

import { monthLabel, monthRange } from '@/lib/month-utils'

import BudgetsPage from '@/pages/budgets'
import { renderWithProviders, t } from '@/test/utils'
import type { Budget, BudgetVsActual, Category } from '@/types'

const api = vi.hoisted(() => ({
  activeAccountIds: null as string[] | null,
  canWrite: true,
  privacyMode: false,
  mask: vi.fn((value: string) => value),
  dashboard: { spendingByCategory: vi.fn(), projectedTransactions: vi.fn() },
  transactions: { list: vi.fn() },
  admin: { accountingMode: vi.fn() },
  budgets: { list: vi.fn(), comparison: vi.fn(), suggestions: vi.fn(), createBatch: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  categories: { listIncludingHidden: vi.fn() },
  categoryGroups: { listIncludingHidden: vi.fn() },
}))

vi.mock('@/lib/api', () => ({
  budgets: api.budgets, categories: api.categories, categoryGroups: api.categoryGroups,
  dashboard: api.dashboard, transactions: api.transactions, admin: api.admin,
}))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: api.canWrite }) }))
vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: api.activeAccountIds }),
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: api.mask, privacyMode: api.privacyMode, MASK: '••••' }),
}))

function category(id: string, name: string, is_hidden = false): Category {
  return { id, user_id: 'u', group_id: 'g', name, icon: 'circle-help', color: '#000', is_system: false, is_hidden, treat_as_transfer: false, is_ignored: false }
}

function comparisonRow(categoryId: string, actual: number): BudgetVsActual {
  return {
    category_id: categoryId, category_name: categoryId, category_icon: 'x', category_color: '#000', group_id: null, group_name: null,
    budget_amount: null, actual_amount: actual, projected_amount: actual, prev_month_amount: 0, projected_prev_month_amount: 0,
    percentage_used: null, is_recurring: false,
  }
}

const rent: Budget = { id: 'b-rent', user_id: 'u', category_id: 'rent', amount: 900, month: '2026-08-01', is_recurring: true }
const hiddenBudget: Budget = { id: 'b-hidden', user_id: 'u', category_id: 'hidden', amount: 120, month: '2026-08-01', is_recurring: true }
const foodTombstone: Budget = { id: 'b-food-zero', user_id: 'u', category_id: 'food', amount: 0, month: '2026-09-01', is_recurring: true }

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(Date.UTC(2026, 9, 10, 12)))
  vi.clearAllMocks()
  api.activeAccountIds = null
  api.canWrite = true
  api.privacyMode = false
  api.mask.mockImplementation(value => value)
  api.dashboard.spendingByCategory.mockResolvedValue([])
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
  api.admin.accountingMode.mockResolvedValue({ mode: 'cash' })
  api.budgets.list.mockResolvedValue([rent, hiddenBudget, foodTombstone])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.listIncludingHidden.mockResolvedValue([{
    id: 'g', user_id: 'u', name: 'Housing', icon: 'home', color: '#000', position: 0, is_system: false, is_hidden: false,
    categories: [category('rent', 'Rent'), category('food', 'Groceries'), category('hidden', 'Archived utilities', true)],
  }])
  api.budgets.suggestions.mockResolvedValue({
    month: '2026-10-01', months_requested: 3, months_analyzed: 3,
    suggestions: [{
      category_id: 'food', category_name: 'Groceries', category_icon: 'x', category_color: '#000', group_id: 'g', group_name: 'Housing',
      suggested_amount: '880', total_spent: '2640', months_with_data: 3, existing_budget: false,
    }],
  })
  api.budgets.createBatch.mockResolvedValue([hiddenBudget])
  api.budgets.comparison.mockImplementation(async (month: string) => ({
    '2026-09-01': [comparisonRow('food', 300)],
    '2026-08-01': [comparisonRow('food', 2000)],
    '2026-07-01': [comparisonRow('food', 340)],
  }[month] ?? []))
  api.budgets.update.mockResolvedValue(rent)
  api.budgets.create.mockResolvedValue(rent)
})

afterEach(() => vi.useRealTimers())

describe('Budget settings page', () => {
  it('keeps hidden category budgets manageable and uses the history suggestion dialog', async () => {
    const { user } = renderWithProviders(<BudgetsPage />)

    expect(await screen.findByRole('textbox', { name: t('budgets.budgetFor', { category: 'Archived utilities' }) })).toHaveValue('120')
    expect(screen.getByText(t('budgets.hiddenCategory'))).toBeInTheDocument()
    expect(await screen.findByText(t('budgets.average', { amount: '$880.00' }))).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('budgets.suggest') }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(api.budgets.suggestions).toHaveBeenCalledWith('2026-10-01', 3)
    await user.click(screen.getByRole('button', { name: t('budgets.suggestCreate') }))

    await waitFor(() => {
      expect(api.budgets.createBatch).toHaveBeenCalledWith({
        month: '2026-10-01', is_recurring: false, items: [{ category_id: 'food', amount: 880 }],
      })
    })
  })

  it('saves budget edits together and persists a recurring toggle-off without changing amount', async () => {
    const { user } = renderWithProviders(<BudgetsPage />)
    const rentInput = await screen.findByRole('textbox', { name: t('budgets.budgetFor', { category: 'Rent' }) })
    await user.clear(rentInput)
    await user.type(rentInput, '950')
    await user.click(screen.getByRole('button', { name: t('budgets.repeatEveryMonthFor', { category: 'Archived utilities' }) }))
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => {
      expect(api.budgets.create).toHaveBeenCalledWith({ category_id: 'hidden', amount: 120, month: '2026-10-01', is_recurring: false })
      expect(api.budgets.update).toHaveBeenCalledWith('b-hidden', { amount: 0, effective_month: '2026-11-01' })
    })
    expect(api.budgets.update).toHaveBeenCalledWith('b-rent', { amount: 950, effective_month: '2026-10-01' })
    expect(api.budgets.delete).not.toHaveBeenCalledWith('b-food-zero')
  })
})


const selectedMonth = '2026-10-01'

function budgetInput(name = 'Rent') {
  return screen.getByRole('textbox', { name: t('budgets.budgetFor', { category: name }) })
}

function categoryTrigger(name = 'Rent', month = '2026-10') {
  return screen.getByRole('button', { name: t('dashboard.drillDownCategory', { category: name, month: monthLabel(month, 'en') }) })
}

function budgetRow(name = 'Rent') {
  const trigger = categoryTrigger(name)
  let row = budgetInput(name).parentElement
  while (row && !row.contains(trigger)) row = row.parentElement
  if (!row) throw new Error(`No budget row found for ${name}`)
  return within(row)
}

function expectNoBudgetWrites() {
  expect(api.budgets.create).not.toHaveBeenCalled()
  expect(api.budgets.update).not.toHaveBeenCalled()
  expect(api.budgets.delete).not.toHaveBeenCalled()
  expect(api.budgets.createBatch).not.toHaveBeenCalled()
}

function mockSpending(amount = 450) {
  api.dashboard.spendingByCategory.mockResolvedValue([
    { category_id: 'rent', total: 100, projected_total: amount },
  ])
}

describe('Budget spending in the inline editor', () => {
  it('uses projected spending, retains zero-activity budgets, and avoids repeating the editable limit', async () => {
    api.dashboard.spendingByCategory.mockResolvedValue([
      { category_id: 'rent', total: 100, projected_total: 450 },
      { category_id: 'unbudgeted', total: 99, projected_total: 99 },
    ])
    renderWithProviders(<BudgetsPage />)

    expect(await screen.findByText('$450.00')).toBeInTheDocument()
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByRole('progressbar', { name: `${t('dashboard.spendingByCategory')}: Rent` })).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: `${t('dashboard.spendingByCategory')}: Archived utilities` })).toBeInTheDocument()
    expect(budgetRow('Archived utilities').getByText('$0.00')).toBeInTheDocument()
    expect(budgetRow('Archived utilities').getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
    expect(budgetRow('Groceries').queryByRole('progressbar')).not.toBeInTheDocument()
    expect(budgetInput()).toHaveValue('900')
    expect(screen.queryByText('$99.00')).not.toBeInTheDocument()
    expect(screen.queryByText(t('dashboard.ofBudget', { budget: '$900.00' }))).not.toBeInTheDocument()
    expect(screen.getByText(t('budgets.spendingDescription'))).toBeInTheDocument()
    expect(api.dashboard.spendingByCategory).toHaveBeenCalledWith(selectedMonth, undefined)
  })

  it.each([true, false])('places regular-weight spending beside the limit on desktop and below the category on mobile (canWrite=%s)', async (canWrite) => {
    api.canWrite = canWrite
    mockSpending()
    renderWithProviders(<BudgetsPage />)
    await screen.findByText('$450.00')
    const row = budgetRow()
    const spending = row.getByRole('progressbar').parentElement?.parentElement

    expect(spending).toHaveClass('row-start-2', 'col-span-3', 'md:row-start-1', 'md:col-start-3', 'md:col-span-1')
    expect(spending?.parentElement).toHaveClass('grid')
    expect(budgetInput().parentElement).toHaveClass('col-start-3', 'row-start-1', 'md:col-start-5')
    expect(row.getByText('$450.00')).toHaveClass('font-normal')
    expect(row.getByText('$450.00')).not.toHaveClass('font-semibold', 'font-bold')
    expect(budgetInput()).toHaveClass('font-semibold')
    expect(row.queryByText(t('dashboard.ofBudget', { budget: '$900.00' }))).not.toBeInTheDocument()
  })

  it.each([
    [0, 0, 'bg-emerald-500'],
    [250, 50, 'bg-emerald-500'],
    [400, 80, 'bg-amber-400'],
    [500, 100, 'bg-amber-400'],
    [750, 100, 'bg-rose-500'],
  ])('uses the shared Overview progress thresholds for spending %s', async (amount, percentage, color) => {
    api.budgets.list.mockResolvedValue([{ ...rent, amount: 500 }])
    mockSpending(amount)
    renderWithProviders(<BudgetsPage />)

    await screen.findByRole('textbox', { name: t('budgets.budgetFor', { category: 'Rent' }) })
    await waitFor(() => expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', String(percentage)))
    expect(budgetRow().getByRole('progressbar').firstElementChild).toHaveStyle({ width: `${percentage}%` })
    expect(budgetRow().getByRole('progressbar').firstElementChild).toHaveClass(color)
  })

  it('previews a valid draft without persisting anything before Save', async () => {
    mockSpending()
    const { user } = renderWithProviders(<BudgetsPage />)
    await screen.findByText('$450.00')
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')

    await user.clear(budgetInput())
    await user.type(budgetInput(), '600')
    await user.tab()

    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '75')
    expect(budgetRow().getByText('$450.00')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('common.save') })).toBeEnabled()
    expectNoBudgetWrites()

    await user.click(screen.getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.budgets.update).toHaveBeenCalledWith('b-rent', {
      amount: 600, effective_month: selectedMonth,
    }))
    expect(api.budgets.update).toHaveBeenCalledTimes(1)
  })

  it('restores the saved amount, recurrence, and progress when edits are cancelled', async () => {
    mockSpending()
    const { user } = renderWithProviders(<BudgetsPage />)
    await screen.findByText('$450.00')
    await user.clear(budgetInput())
    await user.type(budgetInput(), '1800')
    const repeat = screen.getByRole('button', { name: t('budgets.repeatEveryMonthFor', { category: 'Rent' }) })
    await user.click(repeat)
    expect(repeat).toHaveAttribute('aria-pressed', 'false')
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25')
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(api.transactions.list).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: t('common.cancel') }))
    expect(budgetInput()).toHaveValue('900')
    expect(repeat).toHaveAttribute('aria-pressed', 'true')
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByRole('button', { name: t('common.save') })).toBeDisabled()
    expectNoBudgetWrites()
  })

  it.each(['', '0', 'invalid', '-10'])('omits draft progress for the nonpositive or invalid limit "%s"', async (draft) => {
    mockSpending()
    const { user } = renderWithProviders(<BudgetsPage />)
    await screen.findByText('$450.00')
    await user.clear(budgetInput())
    if (draft) await user.type(budgetInput(), draft)

    expect(budgetRow().queryByRole('progressbar')).not.toBeInTheDocument()
    expect(budgetRow().getByText('$450.00')).toBeInTheDocument()
    expectNoBudgetWrites()
    if (draft === 'invalid' || draft === '-10') {
      expect(budgetInput()).toHaveAttribute('aria-invalid', 'true')
      expect(budgetRow().getByRole('alert')).toHaveTextContent(t('budgets.invalidAmount'))
      expect(screen.getByRole('button', { name: t('common.save') })).toBeDisabled()
    }
  })

  it('opens the real panel by keyboard without losing drafts, and restores focus when closed', async () => {
    mockSpending()
    api.transactions.list.mockResolvedValue({ items: [{
      id: 'tx-rent', description: 'October rent payment', date: selectedMonth,
      type: 'debit', amount: 450, currency: 'USD', status: 'posted',
    }], total: 1 })
    const { user } = renderWithProviders(<BudgetsPage />)
    await screen.findByText('$450.00')
    await user.clear(budgetInput())
    await user.type(budgetInput(), '1200')
    const repeat = screen.getByRole('button', { name: t('budgets.repeatEveryMonthFor', { category: 'Rent' }) })
    await user.click(repeat)
    expect(api.transactions.list).not.toHaveBeenCalled()

    const trigger = categoryTrigger()
    trigger.focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByText('October rent payment')).toBeInTheDocument()
    expect(screen.getByRole('complementary')).toHaveFocus()
    expect(api.transactions.list).toHaveBeenCalledWith(expect.objectContaining({
      category_id: 'rent', type: 'debit', ...monthRange('2026-10'),
      account_ids: undefined, user_pnl_only: true,
    }))
    expect(budgetInput()).toHaveValue('1200')
    expect(repeat).toHaveAttribute('aria-pressed', 'false')
    expectNoBudgetWrites()

    await user.click(screen.getByRole('button', { name: t('common.close') }))
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    expect(budgetInput()).toHaveValue('1200')
    expect(repeat).toHaveAttribute('aria-pressed', 'false')
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '37.5')

    await user.keyboard(' ')
    expect(await screen.findByText('October rent payment')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    expect(budgetInput()).toHaveValue('1200')
    expectNoBudgetWrites()
  })

  it('updates spending and the open panel when accounts or month change', async () => {
    api.activeAccountIds = ['account-1']
    api.dashboard.spendingByCategory.mockImplementation(async (month, accountIds) => [{
      category_id: 'rent', projected_total: month === '2026-09-01' ? 75 : accountIds?.[0] === 'account-2' ? 225 : 450,
    }])
    const { user, rerender } = renderWithProviders(<BudgetsPage />)
    await screen.findByText('$450.00')
    await user.click(categoryTrigger())
    expect(await screen.findByText(t('dashboard.drillDownEmpty'))).toBeInTheDocument()
    expect(api.transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({
      category_id: 'rent', account_ids: ['account-1'], ...monthRange('2026-10'),
    }))
    expect(api.dashboard.spendingByCategory).toHaveBeenCalledWith(selectedMonth, ['account-1'])

    api.activeAccountIds = ['account-2']
    rerender(<BudgetsPage />)
    expect(await screen.findByText('$225.00')).toBeInTheDocument()
    await waitFor(() => expect(api.transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({ account_ids: ['account-2'] })))
    expect(api.dashboard.spendingByCategory).toHaveBeenLastCalledWith(selectedMonth, ['account-2'])

    screen.getByRole('button', { name: t('budgets.previousMonth') }).focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByText('$75.00')).toBeInTheDocument()
    expect(screen.getByRole('complementary')).toBeInTheDocument()
    await waitFor(() => expect(api.transactions.list).toHaveBeenLastCalledWith(expect.objectContaining({
      category_id: 'rent', account_ids: ['account-2'], ...monthRange('2026-09'),
    })))
    expect(api.dashboard.spendingByCategory).toHaveBeenLastCalledWith('2026-09-01', ['account-2'])
    expect(api.dashboard.projectedTransactions).toHaveBeenLastCalledWith({ month: '2026-09-01' })
  })

  it('honors the month selected in the URL for spending and the panel', async () => {
    mockSpending()
    const { user } = renderWithProviders(<BudgetsPage />, { route: '/budgets?month=2026-08' })
    await screen.findByText('$450.00')
    expect(api.dashboard.spendingByCategory).toHaveBeenCalledWith('2026-08-01', undefined)
    await user.click(categoryTrigger('Rent', '2026-08'))
    await screen.findByText(t('dashboard.drillDownEmpty'))
    expect(api.transactions.list).toHaveBeenCalledWith(expect.objectContaining({
      category_id: 'rent', ...monthRange('2026-08'),
    }))
  })

  it('does not apply a draft limit to another month when navigating', async () => {
    mockSpending()
    const { user } = renderWithProviders(<BudgetsPage />)
    await screen.findByText('$450.00')
    await user.clear(budgetInput())
    await user.type(budgetInput(), '600')
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '75')

    await user.click(screen.getByRole('button', { name: t('budgets.previousMonth') }))
    await waitFor(() => expect(api.dashboard.spendingByCategory).toHaveBeenLastCalledWith('2026-09-01', undefined))
    expect(budgetInput()).toHaveValue('900')
    expect(screen.getByRole('button', { name: t('common.save') })).toBeDisabled()
    expectNoBudgetWrites()
  })

  it('keeps zero-activity categories accessible and never fetches all accounts for an empty collection', async () => {
    api.activeAccountIds = []
    const { user } = renderWithProviders(<BudgetsPage />)
    await screen.findByRole('textbox', { name: t('budgets.budgetFor', { category: 'Rent' }) })
    expect(budgetRow().getByText('$0.00')).toBeInTheDocument()
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
    expect(api.dashboard.spendingByCategory).not.toHaveBeenCalled()

    await user.click(categoryTrigger('Groceries'))
    expect(await screen.findByText(t('dashboard.drillDownEmpty'))).toBeInTheDocument()
    expect(api.transactions.list).not.toHaveBeenCalled()
    expect(api.dashboard.projectedTransactions).not.toHaveBeenCalled()
    expectNoBudgetWrites()
  })

  it('keeps transaction access available in a read-only workspace without enabling edits', async () => {
    api.canWrite = false
    mockSpending()
    const { user } = renderWithProviders(<BudgetsPage />)
    await screen.findByText('$450.00')
    expect(budgetInput()).toBeDisabled()
    expect(screen.getByRole('button', { name: t('budgets.repeatEveryMonthFor', { category: 'Rent' }) })).toBeDisabled()
    expect(screen.queryByRole('button', { name: t('common.save') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('budgets.suggest') })).not.toBeInTheDocument()
    expect(categoryTrigger()).toBeEnabled()

    await user.click(categoryTrigger())
    expect(await screen.findByText(t('dashboard.drillDownEmpty'))).toBeInTheDocument()
    expect(api.transactions.list).toHaveBeenCalledWith(expect.objectContaining({ category_id: 'rent' }))
    expectNoBudgetWrites()
  })

  it.each([null, ['account-1']])('uses viewer shares only for the unfiltered panel scope (%s)', async (accountIds) => {
    api.activeAccountIds = accountIds
    api.transactions.list.mockResolvedValue({ items: [{
      id: 'split', description: 'Shared rent', date: selectedMonth, type: 'debit',
      amount: 120, amount_primary: 240, viewer_share: 30, currency: 'EUR', status: 'posted',
    }], total: 1 })
    const { user } = renderWithProviders(<BudgetsPage />)
    await screen.findByRole('textbox', { name: t('budgets.budgetFor', { category: 'Rent' }) })
    await user.click(categoryTrigger())
    const panel = within(screen.getByRole('complementary'))
    await panel.findByText('Shared rent')
    expect(panel.getByText(accountIds ? '-€120.00' : '-€30.00')).toBeInTheDocument()
    expect(panel.getAllByText(accountIds ? '$240.00' : '$60.00').length).toBeGreaterThan(0)
  })

  it('masks spending, progress descriptions, and panel transaction amounts', async () => {
    api.privacyMode = true
    api.mask.mockImplementation(() => '••••')
    mockSpending(123.45)
    api.transactions.list.mockResolvedValue({ items: [{
      id: 'private-rent', description: 'Private rent payment', date: selectedMonth,
      type: 'debit', amount: 123.45, currency: 'USD', status: 'posted',
    }], total: 1 })
    const { user } = renderWithProviders(<BudgetsPage />)
    await waitFor(() => expect(api.mask).toHaveBeenCalledWith('$123.45'))
    expect(screen.queryByText('$123.45')).not.toBeInTheDocument()
    for (const bar of screen.getAllByRole('progressbar')) {
      expect(bar).toHaveAttribute('aria-valuetext', '••••')
    }
    await user.click(categoryTrigger())
    expect(await screen.findByText('Private rent payment')).toBeInTheDocument()
    expect(screen.queryByText(/123\.45/)).not.toBeInTheDocument()
    expectNoBudgetWrites()
  })

  it('masks idle budget inputs, reveals only the focused limit, and preserves the numeric draft when remasking', async () => {
    api.privacyMode = true
    api.mask.mockImplementation(() => '••••')
    mockSpending()
    const { user } = renderWithProviders(<BudgetsPage />)
    await screen.findByRole('textbox', { name: t('budgets.budgetFor', { category: 'Rent' }) })
    expect(budgetInput()).toHaveValue('••••')
    expect(budgetInput('Archived utilities')).toHaveValue('••••')

    await user.click(budgetInput())
    expect(budgetInput()).toHaveValue('900')
    expect(budgetInput('Archived utilities')).toHaveValue('••••')
    await user.clear(budgetInput())
    await user.type(budgetInput(), '1200')
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '37.5')
    await user.click(categoryTrigger())
    await screen.findByText(t('dashboard.drillDownEmpty'))
    expect(budgetInput()).toHaveValue('••••')
    expect(screen.queryByDisplayValue('1200')).not.toBeInTheDocument()
    await user.keyboard('{Escape}')
    await user.click(budgetInput())
    expect(budgetInput()).toHaveValue('1200')
    expectNoBudgetWrites()

    await user.click(screen.getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.budgets.update).toHaveBeenCalledWith('b-rent', {
      amount: 1200, effective_month: selectedMonth,
    }))
  })

  it('does not show zero spending or a progress bar while spending is still loading', async () => {
    let resolveSpending!: (value: unknown[]) => void
    api.dashboard.spendingByCategory.mockReturnValue(new Promise(resolve => { resolveSpending = resolve }))
    renderWithProviders(<BudgetsPage />)
    await screen.findByRole('textbox', { name: t('budgets.budgetFor', { category: 'Rent' }) })
    expect(budgetRow().queryByText('$0.00')).not.toBeInTheDocument()
    expect(budgetRow().queryByRole('progressbar')).not.toBeInTheDocument()
    expect(budgetRow().getByText('—')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await act(async () => { resolveSpending([{ category_id: 'rent', projected_total: 450 }]) })
    expect(await screen.findByText('$450.00')).toBeInTheDocument()
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
  })

  it('reports a spending error instead of showing misleading zero totals, and allows retry', async () => {
    api.dashboard.spendingByCategory.mockRejectedValue(new Error('unavailable'))
    const { user } = renderWithProviders(<BudgetsPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent(t('budgets.spendingError'))
    expect(budgetRow().queryByText('$0.00')).not.toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(budgetRow().getByText('—')).toBeInTheDocument()
    expect(budgetInput()).toHaveValue('900')

    mockSpending()
    await user.click(screen.getByRole('button', { name: t('common.retry') }))
    expect(await screen.findByText('$450.00')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
  })

  it('ignores previous spending errors when selecting a collection with no accounts', async () => {
    api.dashboard.spendingByCategory.mockRejectedValue(new Error('unavailable'))
    const { rerender } = renderWithProviders(<BudgetsPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent(t('budgets.spendingError'))
    const calls = api.dashboard.spendingByCategory.mock.calls.length
    api.activeAccountIds = []
    rerender(<BudgetsPage />)
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(budgetRow().getByText('$0.00')).toBeInTheDocument()
    expect(budgetRow().getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
    expect(api.dashboard.spendingByCategory).toHaveBeenCalledTimes(calls)
  })
})
