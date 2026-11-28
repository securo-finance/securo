import { act, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useWorkspace } from '@/contexts/workspace-context'
import { WorkspaceProvider } from '@/contexts/workspace-provider'
import { renderWithProviders } from '@/test/utils'
import type { Workspace, WorkspaceStatus } from '@/types'

import { WorkspaceSuspendedNotice } from './workspace-suspended-notice'

// The real provider, so the poll is checked for what it does to the shell and
// not only for being called.
const workspacesApi = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('@/lib/api', () => ({ workspaces: workspacesApi, WORKSPACE_STORAGE_KEY: 'workspace_id' }))
const useAuth = vi.hoisted(() => vi.fn())
vi.mock('@/contexts/auth-context', () => ({ useAuth }))

function workspace(status: WorkspaceStatus): Workspace {
  return {
    id: 'ws-1',
    name: 'Personal',
    kind: 'personal',
    role: 'owner',
    status,
    enabled_modules: ['transactions'],
  } as Workspace
}

const seen: { isLoading: boolean; canWrite: boolean }[] = []

function Shell() {
  const { isLoading, canWrite } = useWorkspace()
  seen.push({ isLoading, canWrite })
  return (
    <>
      <span>{canWrite ? 'writable' : 'read only'}</span>
      <WorkspaceSuspendedNotice />
    </>
  )
}

async function renderShell() {
  renderWithProviders(
    <WorkspaceProvider>
      <Shell />
    </WorkspaceProvider>,
  )
  await act(() => vi.advanceTimersByTimeAsync(0))
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  seen.length = 0
  useAuth.mockReturnValue({ user: { id: 'u1' }, token: 'jwt', isLoading: false })
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

it('tells the people of a suspended workspace', async () => {
  workspacesApi.list.mockResolvedValue([workspace('suspended')])
  await renderShell()
  expect(screen.getByRole('status')).toHaveTextContent('This workspace is suspended')
})

it('stays out of the way of an active one, and does not ask again', async () => {
  workspacesApi.list.mockResolvedValue([workspace('active')])
  await renderShell()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()

  await act(() => vi.advanceTimersByTimeAsync(60_000))
  expect(workspacesApi.list).toHaveBeenCalledTimes(1)
})

it('asks again in the background, and a reactivation turns the workspace writable', async () => {
  workspacesApi.list.mockResolvedValueOnce([workspace('suspended')])
  await renderShell()
  expect(screen.getByText('read only')).toBeInTheDocument()

  let answer: (list: Workspace[]) => void = () => {}
  workspacesApi.list.mockReturnValueOnce(new Promise<Workspace[]>((resolve) => { answer = resolve }))
  seen.length = 0
  await act(() => vi.advanceTimersByTimeAsync(30_000))
  expect(workspacesApi.list).toHaveBeenCalledTimes(2)
  // While the check is out, the shell keeps showing the workspace.
  expect(seen.some((s) => s.isLoading)).toBe(false)
  expect(screen.getByRole('status')).toBeInTheDocument()

  await act(async () => { answer([workspace('active')]) })
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('writable')).toBeInTheDocument()
  expect(seen.some((s) => s.isLoading)).toBe(false)
})

it('keeps the workspace on screen when a check fails', async () => {
  workspacesApi.list
    .mockResolvedValueOnce([workspace('suspended')])
    .mockRejectedValueOnce(new Error('network'))
  await renderShell()

  await act(() => vi.advanceTimersByTimeAsync(30_000))
  expect(workspacesApi.list).toHaveBeenCalledTimes(2)
  expect(screen.getByRole('status')).toHaveTextContent('This workspace is suspended')
  expect(screen.getByText('read only')).toBeInTheDocument()
})
