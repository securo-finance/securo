import { useState, type ReactNode } from 'react'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithProviders, t } from '@/test/utils'
import { WorkspaceContext } from '@/contexts/workspace-context'
import type { Category } from '@/types'

const { create } = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock('@/lib/api', () => ({ categories: { create } }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { CategorySelect } from './category-select'

const groceries = {
  id: 'cat-1',
  name: 'Groceries',
  icon: 'shopping-cart',
  color: '#10b981',
  group_id: null,
} as unknown as Category

function withRole(canWrite: boolean) {
  return function Workspace({ children }: { children: ReactNode }) {
    return (
      <WorkspaceContext.Provider
        value={{ canWrite } as unknown as React.ContextType<typeof WorkspaceContext>}
      >
        {children}
      </WorkspaceContext.Provider>
    )
  }
}

function renderSelect({ canWrite = true, creatable = true } = {}) {
  const onChange = vi.fn()
  const Workspace = withRole(canWrite)
  // Holds the value like a real form does, so a test sees what the trigger
  // shows after a change rather than only that a callback ran.
  function Harness() {
    const [value, setValue] = useState('')
    return (
      <CategorySelect
        value={value}
        onChange={(v) => { setValue(v); onChange(v) }}
        categories={[groceries]}
        groups={[]}
        allowNone
        creatable={creatable}
      />
    )
  }
  const utils = renderWithProviders(
    <Workspace>
      <Harness />
    </Workspace>,
  )
  return { ...utils, onChange }
}

async function openAndType(user: ReturnType<typeof renderWithProviders>['user'], text: string) {
  await user.click(screen.getByRole('button'))
  await user.type(screen.getByPlaceholderText(t('transactions.searchCategory')), text)
}

describe('CategorySelect inline creation', () => {
  beforeEach(() => {
    create.mockReset()
  })

  it('creates the searched category and selects it', async () => {
    create.mockResolvedValue({ ...groceries, id: 'cat-new', name: 'Pet food', color: '#6366f1' })
    const { user, onChange, queryClient } = renderSelect()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    await openAndType(user, 'Pet food')
    await user.click(screen.getByText(t('common.createNamed', { name: 'Pet food' })))

    await waitFor(() => expect(onChange).toHaveBeenCalledWith('cat-new'))
    // The supplied list has not refetched, yet the trigger shows the new category.
    expect(screen.getByRole('button', { name: 'Pet food' })).toBeInTheDocument()
    expect(create).toHaveBeenCalledWith({ name: 'Pet food', icon: 'circle-help', color: '#6366f1' })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['categories'] })
  })

  it('does not offer to create a name that already exists', async () => {
    const { user } = renderSelect()

    await openAndType(user, 'groceries')

    expect(screen.queryByText(t('common.createNamed', { name: 'groceries' }))).not.toBeInTheDocument()
  })

  it('never offers creation to a viewer', async () => {
    const { user } = renderSelect({ canWrite: false })

    await openAndType(user, 'Pet food')

    expect(screen.queryByText(t('common.createNamed', { name: 'Pet food' }))).not.toBeInTheDocument()
  })

  it('does not offer creation unless the picker opts in', async () => {
    const { user } = renderSelect({ creatable: false })

    await openAndType(user, 'Pet food')

    expect(screen.queryByText(t('common.createNamed', { name: 'Pet food' }))).not.toBeInTheDocument()
  })
})
