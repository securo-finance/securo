import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { toast } from 'sonner'

import { admin, type WorkspacePluggyStatus } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

import { PluggyWorkspaceClients } from './pluggy-workspace-clients'

vi.mock('@/lib/api', () => ({
  admin: {
    workspacePluggyClients: vi.fn(),
    setWorkspacePluggyClient: vi.fn(),
    removeWorkspacePluggyClient: vi.fn(),
  },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const shared: WorkspacePluggyStatus = {
  workspace_id: 'ws-shared',
  name: 'Mom',
  owner_email: 'mom@example.com',
  connections: 0,
  has_credentials: false,
  invalid: false,
}
const own: WorkspacePluggyStatus = { ...shared, workspace_id: 'ws-own', name: 'Mine', has_credentials: true }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(admin.workspacePluggyClients).mockResolvedValue([shared, own])
  vi.mocked(admin.setWorkspacePluggyClient).mockResolvedValue()
  vi.mocked(admin.removeWorkspacePluggyClient).mockResolvedValue()
})

describe('Pluggy clients by workspace', () => {
  it('saves a client for the chosen workspace only once both fields are filled', async () => {
    const { user } = renderWithProviders(<PluggyWorkspaceClients canStoreSecrets />)

    await user.click(await screen.findByRole('button', { name: /: Mom$/ }))
    const form = screen.getByRole('form', { name: 'Mom' })
    const [clientId, clientSecret] = Array.from(form.querySelectorAll('input'))
    const save = within(form).getByRole('button', { name: /save/i })

    await user.type(clientId, 'mom-client')
    expect(save).toBeDisabled()
    await user.type(clientSecret, 'mom-secret')
    await user.click(save)

    await waitFor(() =>
      expect(admin.setWorkspacePluggyClient).toHaveBeenCalledWith('ws-shared', 'mom-client', 'mom-secret'),
    )
  })

  it('explains why a workspace with connections cannot move to another client', async () => {
    vi.mocked(admin.removeWorkspacePluggyClient).mockRejectedValue({ response: { status: 409, data: { detail: 'pluggy_client_change' } } })
    const { user } = renderWithProviders(<PluggyWorkspaceClients canStoreSecrets />)

    const row = (await screen.findByText('Mine')).closest('li') as HTMLElement
    const buttons = within(row).getAllByRole('button')
    await user.click(buttons[0])
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: /delete/i }))

    await waitFor(() => expect(admin.removeWorkspacePluggyClient).toHaveBeenCalledWith('ws-own'))
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(vi.mocked(toast.error).mock.calls[0][0]).toMatch(/connections/i)
  })

  it('shows a weak SECRET_KEY refusal as a plain error, not a client change', async () => {
    vi.mocked(admin.setWorkspacePluggyClient).mockRejectedValue({
      response: { status: 409, data: { detail: 'Set a unique SECRET_KEY of at least 32 characters before saving credentials' } },
    })
    const { user } = renderWithProviders(<PluggyWorkspaceClients canStoreSecrets />)

    await user.click(await screen.findByRole('button', { name: /: Mom$/ }))
    const form = screen.getByRole('form', { name: 'Mom' })
    const [clientId, clientSecret] = Array.from(form.querySelectorAll('input'))
    await user.type(clientId, 'mom-client')
    await user.type(clientSecret, 'mom-secret')
    await user.click(within(form).getByRole('button', { name: /save/i }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(vi.mocked(toast.error).mock.calls[0][0]).not.toMatch(/connections/i)
  })

  it('offers no changes on an instance the operator manages', async () => {
    renderWithProviders(<PluggyWorkspaceClients canStoreSecrets readOnly />)

    const row = (await screen.findByText('Mine')).closest('li') as HTMLElement
    expect(within(row).queryAllByRole('button')).toHaveLength(0)
    expect(within(row).getByText('Configured by Securo')).toBeInTheDocument()
  })

  it('blocks saving when the instance cannot store secrets', async () => {
    renderWithProviders(<PluggyWorkspaceClients canStoreSecrets={false} />)
    expect(await screen.findByRole('button', { name: /: Mom$/ })).toBeDisabled()
  })
})
