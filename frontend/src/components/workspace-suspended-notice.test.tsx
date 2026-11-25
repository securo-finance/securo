import { screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { useWorkspace } from '@/contexts/workspace-context'
import { renderWithProviders } from '@/test/utils'

import { WorkspaceSuspendedNotice } from './workspace-suspended-notice'

vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: vi.fn() }))

function inWorkspace(status: string) {
  vi.mocked(useWorkspace).mockReturnValue({ current: { status } } as never)
}

it('tells the people of a suspended workspace', () => {
  inWorkspace('suspended')
  renderWithProviders(<WorkspaceSuspendedNotice />)
  expect(screen.getByRole('status')).toHaveTextContent('This workspace is suspended')
})

it('stays out of the way of an active one', () => {
  inWorkspace('active')
  renderWithProviders(<WorkspaceSuspendedNotice />)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})
