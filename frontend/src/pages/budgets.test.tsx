import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import BudgetsPage from '@/pages/budgets'
import { renderWithProviders, t } from '@/test/utils'
import type { Budget, BudgetVsActual, Category } from '@/types'

const api = vi.hoisted(() => ({
  budgets: { list: vi.fn(), comparison: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => ({ budgets: api.budgets, categories: api.categories, categoryGroups: api.categoryGroups }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: true }) }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

function category(id: string, name: string): Category {
  return { id, user_id: 'u', group_id: 'g', name, icon: 'circle-help', color: '#000', is_system: false, is_hidden: false, treat_as_transfer: false, is_ignored: false }
}

function comparisonRow(categoryId: string, actual: number): BudgetVsActual {
  return {
    category_id: categoryId, category_name: categoryId, category_icon: 'x', category_color: '#000', group_id: null, group_name: null,
    budget_amount: null, actual_amount: actual, projected_amount: actual, prev_month_amount: 0, projected_prev_month_amount: 0,
    percentage_used: null, is_recurring: false,
  }
}

const rent: Budget = { id: 'b-rent', user_id: 'u', category_id: 'rent', amount: 900, month: '2026-08-01', is_recurring: true }

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(Date.UTC(2026, 9, 10, 12)))
  vi.clearAllMocks()
  api.budgets.list.mockResolvedValue([rent])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([{
    id: 'g', user_id: 'u', name: 'Housing', icon: 'home', color: '#000', position: 0, is_system: false, is_hidden: false,
    categories: [category('rent', 'Rent'), category('food', 'Groceries')],
  }])
  // Groceries: 300, 340 and a one-off 2000 month; the suggestion is the median.
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
  it('suggests the median month and saves every change in one go', async () => {
    const { user } = renderWithProviders(<BudgetsPage />)

    expect(await screen.findByText(t('budgets.average', { amount: '$340.00' }), { exact: false })).toBeInTheDocument()

    await user.clear(screen.getByRole('textbox', { name: t('budgets.budgetFor', { category: 'Rent' }) }))
    await user.type(screen.getByRole('textbox', { name: t('budgets.budgetFor', { category: 'Rent' }) }), '950')
    await user.click(screen.getByRole('button', { name: t('budgets.useSuggestion') }))
    // Groceries only for this month.
    await user.click(screen.getByRole('button', { name: t('budgets.repeatEveryMonthFor', { category: 'Groceries' }) }))
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgets.create).toHaveBeenCalled())
    expect(api.budgets.update).toHaveBeenCalledWith('b-rent', { amount: 950, effective_month: '2026-10-01' })
    expect(api.budgets.create).toHaveBeenCalledWith({ category_id: 'food', amount: 340, month: '2026-10-01', is_recurring: false })
  })
})
