import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { format } from 'date-fns'

import BudgetsPage from '@/pages/budgets'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
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

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
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
    api.budgets.list.mockResolvedValue([])
    api.categories.list.mockResolvedValue(categories)
    api.categories.listIncludingHidden.mockResolvedValue(categories)
    api.categoryGroups.list.mockResolvedValue([])
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
