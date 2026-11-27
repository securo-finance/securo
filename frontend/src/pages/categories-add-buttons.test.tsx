/**
 * The two "+" buttons in the categories page header.
 *
 * Below the `sm` breakpoint the visible text of both buttons is hidden, so an
 * icon alone is all a phone shows — and an icon is not a name. The explicit
 * label is what keeps them named at any width. The test checks that label
 * rather than the rendered text, because jsdom applies no media queries: the
 * text stays in the tree here even at the width where a phone drops it.
 */
import { createContext } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

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

const FOOD = {
  id: 'food',
  user_id: 'user-1',
  name: 'Food',
  icon: 'circle-help',
  color: '#6366f1',
  group_id: null,
  is_system: true,
  is_hidden: false,
  treat_as_transfer: false,
  is_ignored: false,
}

beforeEach(() => {
  vi.clearAllMocks()
  api.categories.listIncludingHidden.mockResolvedValue([FOOD])
  api.categoryGroups.listIncludingHidden.mockResolvedValue([])
})

describe('the add buttons on the categories page', () => {
  it('name both header buttons, so the icon on its own still reads', async () => {
    renderWithProviders(<CategoriesPage />)

    const addGroup = await screen.findByRole('button', { name: 'Add Group' })
    const addCategory = await screen.findByRole('button', { name: 'Add Category' })

    // The label is carried by the button itself, not by the text that a
    // narrow screen hides.
    expect(addGroup).toHaveAttribute('aria-label', 'Add Group')
    expect(addCategory).toHaveAttribute('aria-label', 'Add Category')

    // The visible text is still there for wide screens, and the tooltip
    // word matches the label so the two never disagree.
    expect(addGroup).toHaveTextContent('Add Group')
    expect(addCategory).toHaveTextContent('Add Category')
    expect(addGroup).toHaveAttribute('title', 'Add Group')
    expect(addCategory).toHaveAttribute('title', 'Add Category')
  })
})
