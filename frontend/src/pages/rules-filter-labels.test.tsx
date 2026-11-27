/**
 * The three native selects that filter the rules list.
 *
 * They sit in the toolbar above the list with only their first option as
 * visible text, so a screen reader had three comboboxes with no name at all.
 * The check is on the accessible name, which is what a screen reader
 * announces: `getByRole` with a `name` finds nothing unless the select
 * carries its own label.
 */
import { createContext } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import RulesPage from '@/pages/rules'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  rules: {
    list: vi.fn(),
    packs: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    applyAll: vi.fn(),
    exportFile: vi.fn(),
    importFile: vi.fn(),
    installPack: vi.fn(),
    packRemoval: vi.fn(),
    removePack: vi.fn(),
  },
  categories: {
    list: vi.fn(),
    listIncludingHidden: vi.fn(),
  },
  categoryGroups: {
    list: vi.fn(),
  },
  accounts: {
    list: vi.fn(),
  },
  payees: {
    list: vi.fn(),
  },
  reconciliation: {
    suggestions: vi.fn(),
    rules: vi.fn(),
  },
}))

vi.mock('@/lib/api', () => ({
  rules: api.rules,
  categories: api.categories,
  categoryGroups: api.categoryGroups,
  accounts: api.accounts,
  payees: api.payees,
  reconciliation: api.reconciliation,
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
  WorkspaceContext: createContext<{ canWrite: boolean } | undefined>({ canWrite: true }),
}))

beforeEach(() => {
  vi.clearAllMocks()
  api.rules.list.mockResolvedValue([])
  api.rules.packs.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
  api.reconciliation.suggestions.mockResolvedValue([])
  api.reconciliation.rules.mockResolvedValue([])
})

describe('the rules list filters', () => {
  it('names the three selects, so a screen reader announces what each one filters', async () => {
    renderWithProviders(<RulesPage />)

    const category = await screen.findByRole('combobox', { name: 'Filter by category' })
    const status = screen.getByRole('combobox', { name: 'Filter by status' })
    const action = screen.getByRole('combobox', { name: 'Filter by action' })

    // The visible first option is still the text people read.
    expect(category).toHaveDisplayValue('All categories')
    expect(status).toHaveDisplayValue('All statuses')
    expect(action).toHaveDisplayValue('All actions')
  })

  it('leaves no filter unnamed', async () => {
    renderWithProviders(<RulesPage />)

    await screen.findByRole('combobox', { name: 'Filter by category' })
    const comboboxes = screen.getAllByRole('combobox')

    expect(comboboxes).toHaveLength(3)
    for (const combobox of comboboxes) {
      expect(combobox).toHaveAccessibleName()
    }
  })
})
