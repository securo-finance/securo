import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import BudgetsPage from '@/pages/budgets'
import { renderWithProviders, t } from '@/test/utils'
import type { Budget, BudgetVsActual, Category } from '@/types'

const api = vi.hoisted(() => ({
  budgets: { list: vi.fn(), comparison: vi.fn(), suggestions: vi.fn(), createBatch: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  categories: { listIncludingHidden: vi.fn() },
  categoryGroups: { listIncludingHidden: vi.fn() },
}))

vi.mock('@/lib/api', () => ({ budgets: api.budgets, categories: api.categories, categoryGroups: api.categoryGroups }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US', useDateLocale: () => 'en-US' }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }) }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: true }) }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
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
