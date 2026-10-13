/**
 * Deleting a category from the categories page.
 *
 * The page asks what the category still holds before it deletes anything, so
 * the user is only interrupted when there is something to decide.
 */
import { createContext } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import CategoriesPage from '@/pages/categories'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  categories: {
    listIncludingHidden: vi.fn(),
    usage: vi.fn(),
    delete: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    ruleUsage: vi.fn(),
  },
  categoryGroups: {
    listIncludingHidden: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('@/lib/api', () => ({
  categories: api.categories,
  categoryGroups: api.categoryGroups,
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
  WorkspaceContext: createContext<{ canWrite: boolean } | undefined>({ canWrite: true }),
}))

function category(id: string, name: string, overrides = {}) {
  return {
    id,
    user_id: 'user-1',
    name,
    icon: 'circle-help',
    color: '#6366f1',
    group_id: null,
    is_system: true,
    is_hidden: false,
    treat_as_transfer: false,
    is_ignored: false,
    ...overrides,
  }
}

const FOOD = category('food', 'Food')
const TRANSPORT = category('transport', 'Transport')

const NO_USAGE = { transactions: 0, budgets: 0, recurring_transactions: 0, rules: [] }

beforeEach(() => {
  vi.clearAllMocks()
  api.categories.listIncludingHidden.mockResolvedValue([FOOD, TRANSPORT])
  api.categoryGroups.listIncludingHidden.mockResolvedValue([])
  api.categories.delete.mockResolvedValue(undefined)
})

async function clickDelete(user: ReturnType<typeof renderWithProviders>['user'], name: string) {
  const row = (await screen.findByText(name)).closest('div')!.parentElement!
  await user.click(within(row).getByTitle('Delete'))
}

describe('deleting a category', () => {
  it('goes straight to the plain confirmation when nothing points at it', async () => {
    api.categories.usage.mockResolvedValue(NO_USAGE)
    const { user } = renderWithProviders(<CategoriesPage />)

    await clickDelete(user, 'Food')

    expect(await screen.findByText('Delete category?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Delete$/ }))
    await waitFor(() => expect(api.categories.delete).toHaveBeenCalledWith('food', undefined))
  })

  it('asks where the entries go when the category is still in use', async () => {
    api.categories.usage.mockResolvedValue({
      transactions: 142,
      budgets: 2,
      recurring_transactions: 0,
      rules: [{ id: 'rule-1', name: 'Groceries' }],
    })
    const { user } = renderWithProviders(<CategoriesPage />)

    await clickDelete(user, 'Food')

    expect(await screen.findByText('Delete Food?')).toBeInTheDocument()
    // Only what is actually in use is listed.
    expect(screen.getByText('142')).toBeInTheDocument()
    expect(screen.getByText('Transactions')).toBeInTheDocument()
    expect(screen.queryByText('Recurring')).not.toBeInTheDocument()

    // Nothing is deleted until a destination is chosen.
    const confirm = screen.getByRole('button', { name: /Delete and move/ })
    expect(confirm).toBeDisabled()

    await user.click(screen.getByRole('button', { name: /Select category/ }))
    await user.click(await screen.findByRole('option', { name: /Transport/ }))
    await waitFor(() => expect(confirm).toBeEnabled())

    await user.click(confirm)
    await waitFor(() =>
      expect(api.categories.delete).toHaveBeenCalledWith('food', 'transport'),
    )
  })

  it('does not offer the category being deleted as its own destination', async () => {
    api.categories.usage.mockResolvedValue({ ...NO_USAGE, transactions: 3 })
    const { user } = renderWithProviders(<CategoriesPage />)

    await clickDelete(user, 'Food')
    await screen.findByText('Delete Food?')
    await user.click(screen.getByRole('button', { name: /Select category/ }))

    const options = await screen.findAllByRole('option')
    expect(options.map((option) => option.textContent)).toEqual(['Transport'])
  })
})
