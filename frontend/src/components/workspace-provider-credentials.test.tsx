import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { toast } from 'sonner'

import { admin, type WorkspaceProviderCredentialStatus } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

import { WorkspaceProviderCredentials } from './workspace-provider-credentials'

vi.mock('@/lib/api', () => ({
  admin: {
    workspaceProviderCredentials: vi.fn(),
    setWorkspaceProviderCredentials: vi.fn(),
    removeWorkspaceProviderCredentials: vi.fn(),
  },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const shared: WorkspaceProviderCredentialStatus = {
  workspace_id: 'ws-shared',
  name: 'Mom',
  owner_email: 'mom@example.com',
  connections: 0,
  has_credentials: false,
  invalid: false,
}
const FIELDS = ['pluggy_client_id', 'pluggy_client_secret']
const own: WorkspaceProviderCredentialStatus = { ...shared, workspace_id: 'ws-own', name: 'Mine', has_credentials: true }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(admin.workspaceProviderCredentials).mockResolvedValue([shared, own])
  vi.mocked(admin.setWorkspaceProviderCredentials).mockResolvedValue()
  vi.mocked(admin.removeWorkspaceProviderCredentials).mockResolvedValue()
})

describe('Pluggy clients by workspace', () => {
  it('saves a client for the chosen workspace only once both fields are filled', async () => {
    const { user } = renderWithProviders(<WorkspaceProviderCredentials provider="pluggy" fields={FIELDS} canStoreSecrets />)

    await user.click(await screen.findByRole('button', { name: /: Mom$/ }))
    const form = screen.getByRole('form', { name: 'Mom' })
    const [clientId, clientSecret] = Array.from(form.querySelectorAll('input'))
    const save = within(form).getByRole('button', { name: /save/i })

    await user.type(clientId, 'mom-client')
    expect(save).toBeDisabled()
    await user.type(clientSecret, 'mom-secret')
    await user.click(save)

    await waitFor(() =>
      expect(admin.setWorkspaceProviderCredentials).toHaveBeenCalledWith('pluggy', 'ws-shared', {
        pluggy_client_id: 'mom-client',
        pluggy_client_secret: 'mom-secret',
      }),
    )
  })

  it('explains why a workspace with connections cannot move to another client', async () => {
    vi.mocked(admin.removeWorkspaceProviderCredentials).mockRejectedValue({ response: { status: 409, data: { detail: 'provider_account_change' } } })
    const { user } = renderWithProviders(<WorkspaceProviderCredentials provider="pluggy" fields={FIELDS} canStoreSecrets />)

    await user.click(await screen.findByRole('button', { name: 'Remove: Mine' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: /delete/i }))

    await waitFor(() => expect(admin.removeWorkspaceProviderCredentials).toHaveBeenCalledWith('pluggy', 'ws-own'))
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(vi.mocked(toast.error).mock.calls[0][0]).toMatch(/connections/i)
  })

  it('shows a weak SECRET_KEY refusal as a plain error, not a client change', async () => {
    vi.mocked(admin.setWorkspaceProviderCredentials).mockRejectedValue({
      response: { status: 409, data: { detail: 'Set a unique SECRET_KEY of at least 32 characters before saving credentials' } },
    })
    const { user } = renderWithProviders(<WorkspaceProviderCredentials provider="pluggy" fields={FIELDS} canStoreSecrets />)

    await user.click(await screen.findByRole('button', { name: /: Mom$/ }))
    const form = screen.getByRole('form', { name: 'Mom' })
    const [clientId, clientSecret] = Array.from(form.querySelectorAll('input'))
    await user.type(clientId, 'mom-client')
    await user.type(clientSecret, 'mom-secret')
    await user.click(within(form).getByRole('button', { name: /save/i }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
    expect(vi.mocked(toast.error).mock.calls[0][0]).not.toMatch(/connections/i)
  })

  it('says when the workspaces could not be loaded, and retries', async () => {
    vi.mocked(admin.workspaceProviderCredentials).mockRejectedValueOnce(new Error('down'))
    const { user } = renderWithProviders(<WorkspaceProviderCredentials provider="pluggy" fields={FIELDS} canStoreSecrets />)

    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't load the workspaces.")
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Mine')).toBeInTheDocument()
  })

  it('blocks saving when the instance cannot store secrets', async () => {
    renderWithProviders(<WorkspaceProviderCredentials provider="pluggy" fields={FIELDS} canStoreSecrets={false} />)
    expect(await screen.findByRole('button', { name: /: Mom$/ })).toBeDisabled()
  })
})
