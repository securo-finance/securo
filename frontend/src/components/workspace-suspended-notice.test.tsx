import { act, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { useWorkspace } from '@/contexts/workspace-context'
import { renderWithProviders } from '@/test/utils'

import { WorkspaceSuspendedNotice } from './workspace-suspended-notice'

vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: vi.fn() }))

const refresh = vi.fn()

function inWorkspace(status: string) {
  vi.mocked(useWorkspace).mockReturnValue({ current: { status }, refresh } as never)
}

afterEach(() => {
  vi.useRealTimers()
  refresh.mockClear()
})

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

it('asks again while suspended, so a reactivated workspace turns writable', async () => {
  vi.useFakeTimers()
  inWorkspace('suspended')
  renderWithProviders(<WorkspaceSuspendedNotice />)

  // The session read the list once; nobody clicks anything here.
  expect(refresh).not.toHaveBeenCalled()
  await act(() => vi.advanceTimersByTimeAsync(30_000))
  expect(refresh).toHaveBeenCalledTimes(1)
})

it('does not ask again once the workspace is active', async () => {
  vi.useFakeTimers()
  inWorkspace('active')
  renderWithProviders(<WorkspaceSuspendedNotice />)

  await act(() => vi.advanceTimersByTimeAsync(60_000))
  expect(refresh).not.toHaveBeenCalled()
})
