import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import { admin } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'
import type { AdminWorkspace } from '@/types'

import { WorkspaceStatusSettings } from './workspace-status-settings'

const refresh = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({
  admin: { listWorkspaces: vi.fn(), updateWorkspaceStatus: vi.fn() },
}))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ refresh }) }))

const home: AdminWorkspace = {
  id: 'w1',
  name: 'Home',
  kind: 'personal',
  status: 'active',
  status_changed_at: null,
  owners: ['owner@example.com'],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(admin.listWorkspaces).mockResolvedValue({ items: [home], total: 1 })
  vi.mocked(admin.updateWorkspaceStatus).mockResolvedValue({ ...home, status: 'suspended' })
})

it('asks before suspending', async () => {
  const { user } = renderWithProviders(<WorkspaceStatusSettings />)
  await user.click(await screen.findByRole('button', { name: 'Suspend Home' }))
  expect(admin.updateWorkspaceStatus).not.toHaveBeenCalled()

  const dialog = await screen.findByRole('dialog', { name: 'Suspend Home?' })
  await user.click(within(dialog).getByRole('button', { name: 'Suspend' }))
  await waitFor(() => expect(admin.updateWorkspaceStatus).toHaveBeenCalledWith('w1', 'suspended'))
  await waitFor(() => expect(refresh).toHaveBeenCalled())
  expect(admin.listWorkspaces).toHaveBeenCalledTimes(2)
})

it('reactivates a suspended workspace', async () => {
  vi.mocked(admin.listWorkspaces).mockResolvedValue({
    items: [{ ...home, status: 'suspended' }],
    total: 1,
  })
  const { user } = renderWithProviders(<WorkspaceStatusSettings />)
  expect(await screen.findByText('Suspended')).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Reactivate Home' }))
  await waitFor(() => expect(admin.updateWorkspaceStatus).toHaveBeenCalledWith('w1', 'active'))
})

it('searches on the server and says when there are more', async () => {
  vi.mocked(admin.listWorkspaces).mockResolvedValue({ items: [home], total: 60 })
  const { user } = renderWithProviders(<WorkspaceStatusSettings />)
  expect(await screen.findByText(/Showing 1 of 60/)).toBeVisible()
  await user.type(screen.getByRole('searchbox'), 'x')
  await waitFor(() => expect(admin.listWorkspaces).toHaveBeenLastCalledWith({ search: 'x' }))
})
