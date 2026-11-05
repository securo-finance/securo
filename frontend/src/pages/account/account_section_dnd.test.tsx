import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import type { Account } from '@/types'
import AccountSectionDnd from './account_section_dnd'

const bulkOrdering = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))

vi.mock('@/lib/api', () => ({ accounts: { bulkOrdering } }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ canWrite: true }) }))
vi.mock('@/components/account-icon', () => ({ AccountIcon: () => null }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@dnd-kit/react/sortable', () => ({
  useSortable: () => ({ ref: () => {}, handleRef: () => {}, isDragging: false }),
}))
vi.mock('@dnd-kit/react', async () => {
  const { createElement } = await import('react')
  return {
    DragDropProvider: ({ children, onDragStart, onDragEnd }: {
      children: React.ReactNode
      onDragStart: () => void
      onDragEnd: (event: unknown) => void
    }) => createElement('div', {},
      createElement('button', { onClick: onDragStart }, 'start drag'),
      createElement('button', {
        onClick: () => onDragEnd({
          canceled: false,
          operation: {
            source: { id: 'second', initialIndex: 1, index: 0 },
            // Sortable can report the dragged item as target after projecting
            // its new index; comparing source/target IDs would skip the save.
            target: { id: 'second', index: 0 },
            canceled: false,
          },
        }),
      }, 'end drag'),
      children,
    ),
  }
})

beforeEach(() => bulkOrdering.mockClear())

it('persists reordered active accounts only after clicking save', async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const accounts = [
    { id: 'first', name: 'First', order: 0 },
    { id: 'second', name: 'Second', order: 0 },
  ] as Account[]

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><AccountSectionDnd accounts={accounts} /></MemoryRouter>
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'start drag' }))
  fireEvent.click(screen.getByRole('button', { name: 'end drag' }))
  expect(bulkOrdering).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'common.save' }))

  await waitFor(() => expect(bulkOrdering).toHaveBeenCalledWith([
    { account_id: 'second', order: 0 },
    { account_id: 'first', order: 1 },
  ], expect.anything()))
})
