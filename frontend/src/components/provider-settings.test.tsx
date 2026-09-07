import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/test/utils'
import { ProviderSettings } from './provider-settings'
import { admin } from '@/lib/api'

vi.mock('@/lib/api', () => ({ admin: { providerSettings: vi.fn(), updateProviderSettings: vi.fn() } }))

const provider = {
  name: 'pluggy', configured: true, can_store_secrets: true,
  fields: {
    pluggy_client_id: { configured: true, source: 'environment', invalid: false, environment_configured: true },
    pluggy_client_secret: { configured: true, source: 'app', invalid: false, environment_configured: true },
  },
} as const

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(admin.providerSettings).mockResolvedValue([provider])
  vi.mocked(admin.updateProviderSettings).mockResolvedValue(provider)
})

describe('Provider settings', () => {
  it('keeps saved fields empty and sends only replacements', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ProviderSettings />)
    const secret = await screen.findByLabelText('Client secret')
    expect(secret).toHaveValue('')
    expect(secret).toHaveAttribute('type', 'password')
    await user.type(secret, 'replacement-secret')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(admin.updateProviderSettings).toHaveBeenCalledWith('pluggy', { pluggy_client_secret: 'replacement-secret' }))
    await waitFor(() => expect(secret).toHaveValue(''))
  })

  it('confirms removal before restoring the environment fallback', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ProviderSettings />)
    const secret = await screen.findByLabelText('Client secret')
    await user.click(within(secret.parentElement!).getByRole('button', { name: 'Use environment value' }))
    expect(admin.updateProviderSettings).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/environment value will be used/)).toBeVisible()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(admin.updateProviderSettings).not.toHaveBeenCalled()
    await user.click(within(secret.parentElement!).getByRole('button', { name: 'Use environment value' }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(admin.updateProviderSettings).toHaveBeenCalledWith('pluggy', { pluggy_client_secret: null }))
  })

  it('warns when removing the only credential will disable the provider', async () => {
    vi.mocked(admin.providerSettings).mockResolvedValue([{ ...provider, fields: {
      ...provider.fields, pluggy_client_secret: { ...provider.fields.pluggy_client_secret, environment_configured: false },
    } }])
    const { user } = renderWithProviders(<ProviderSettings />)
    await user.click(await screen.findByRole('button', { name: 'Remove saved value' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/No environment value is available/)).toBeVisible()
    expect(admin.updateProviderSettings).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(admin.updateProviderSettings).toHaveBeenCalledWith('pluggy', { pluggy_client_secret: null }))
  })

  it('explains unsafe server key configuration while allowing removal', async () => {
    vi.mocked(admin.providerSettings).mockResolvedValue([{ ...provider, can_store_secrets: false }])
    const { user } = renderWithProviders(<ProviderSettings />)
    expect(await screen.findByRole('alert')).toHaveTextContent('SECRET_KEY')
    await user.type(screen.getByLabelText('Client secret'), 'replacement')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Use environment value' })).toBeEnabled()
  })
})
