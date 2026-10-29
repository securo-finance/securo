import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { admin, type ProviderSettingStatus } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

import { ProviderSettings } from './provider-settings'

vi.mock('@/lib/api', () => ({
  admin: { providerSettings: vi.fn(), updateProviderSettings: vi.fn() },
}))

const pluggy: ProviderSettingStatus = {
  name: 'pluggy',
  configured: true,
  can_store_secrets: true,
  fields: {
    pluggy_client_id: { configured: true, source: 'environment', invalid: false, environment_configured: true },
    pluggy_client_secret: { configured: true, source: 'app', invalid: false, environment_configured: true },
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(admin.providerSettings).mockResolvedValue([pluggy])
  vi.mocked(admin.updateProviderSettings).mockResolvedValue(pluggy)
})

describe('provider settings', () => {
  it('groups bank connections and exchange rates and provides a setup action for new providers', async () => {
    vi.mocked(admin.providerSettings).mockResolvedValue([
      pluggy,
      {
        name: 'openexchangerates', configured: false, can_store_secrets: true,
        fields: { openexchangerates_app_id: { configured: false, source: 'none', invalid: false, environment_configured: false } },
      },
      { name: 'future_provider', configured: false, can_store_secrets: true, fields: {} },
    ])
    const { user } = renderWithProviders(<ProviderSettings />)

    const connections = await screen.findByRole('region', { name: 'Bank connections' })
    expect(await within(connections).findByRole('button', { name: 'Manage Pluggy' })).toBeVisible()
    const exchangeRates = screen.getByRole('region', { name: 'Exchange rates' })
    expect(within(exchangeRates).getByRole('button', { name: 'Set up Open Exchange Rates' })).toBeVisible()
    const other = screen.getByRole('region', { name: 'Other integrations' })
    expect(within(other).getByRole('button', { name: 'Set up future_provider' })).toBeVisible()

    await user.click(within(connections).getByRole('button', { name: 'Manage Pluggy' }))
    expect(screen.getByRole('link', { name: 'Setup guide' })).toHaveAttribute(
      'href', 'https://docs.usesecuro.com/docs/getting-started/installation#optional-integrations',
    )
    await user.click(screen.getByRole('button', { name: 'Close' }))

    await user.click(within(exchangeRates).getByRole('button', { name: 'Set up Open Exchange Rates' }))
    expect(screen.getByRole('link', { name: 'Setup guide' })).toHaveAttribute(
      'href', 'https://docs.usesecuro.com/docs/getting-started/installation#exchange-rates',
    )
    await user.click(screen.getByRole('button', { name: 'Close' }))

    await user.click(within(other).getByRole('button', { name: 'Set up future_provider' }))
    expect(screen.queryByRole('link', { name: 'Setup guide' })).not.toBeInTheDocument()
  })

  it('leaves saved credentials blank in the dialog and saves only replacements', async () => {
    const { user, queryClient } = renderWithProviders(<ProviderSettings />)
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')

    await user.click(await screen.findByRole('button', { name: 'Manage Pluggy' }))
    const form = screen.getByRole('form', { name: 'Pluggy' })
    const clientId = within(form).getByLabelText('Client ID')
    const clientSecret = within(form).getByLabelText('Client secret')
    expect(clientId).toHaveValue('')
    expect(clientSecret).toHaveValue('')
    expect(clientId).toHaveAttribute('type', 'password')
    expect(clientSecret).toHaveAttribute('type', 'password')
    expect(within(form).getByText('Set by environment')).toBeVisible()
    expect(within(form).getByText('Saved securely')).toBeVisible()

    await user.type(clientSecret, 'replacement-secret')
    await user.click(within(form).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(admin.updateProviderSettings).toHaveBeenCalledWith('pluggy', {
      pluggy_client_secret: 'replacement-secret',
    }))
    await waitFor(() => expect(screen.queryByRole('form', { name: 'Pluggy' })).not.toBeInTheDocument())
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['admin', 'provider-settings'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['connections', 'providers'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['fx-rates'] })
  })

  it('asks before deleting a saved value and explains the environment fallback', async () => {
    const { user } = renderWithProviders(<ProviderSettings />)
    await user.click(await screen.findByRole('button', { name: 'Manage Pluggy' }))
    const form = screen.getByRole('form', { name: 'Pluggy' })
    await user.click(within(form).getByRole('button', { name: 'Use environment value' }))

    const confirmation = screen.getByRole('dialog', { name: 'Remove saved Client secret?' })
    expect(within(confirmation).getByText(/environment value will be used/)).toBeVisible()
    expect(admin.updateProviderSettings).not.toHaveBeenCalled()
    await user.click(within(confirmation).getByRole('button', { name: 'Cancel' }))
    expect(admin.updateProviderSettings).not.toHaveBeenCalled()

    await user.click(within(form).getByRole('button', { name: 'Use environment value' }))
    await user.click(within(screen.getByRole('dialog', { name: 'Remove saved Client secret?' })).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(admin.updateProviderSettings).toHaveBeenCalledWith('pluggy', {
      pluggy_client_secret: null,
    }))
  })

  it('warns when removing a saved value has no environment fallback', async () => {
    vi.mocked(admin.providerSettings).mockResolvedValue([{
      ...pluggy,
      fields: {
        ...pluggy.fields,
        pluggy_client_secret: { ...pluggy.fields.pluggy_client_secret, environment_configured: false },
      },
    }])
    const { user } = renderWithProviders(<ProviderSettings />)
    await user.click(await screen.findByRole('button', { name: 'Manage Pluggy' }))
    const form = screen.getByRole('form', { name: 'Pluggy' })
    await user.click(within(form).getByRole('button', { name: 'Remove saved value' }))
    const confirmation = screen.getByRole('dialog', { name: 'Remove saved Client secret?' })
    expect(within(confirmation).getByText(/No environment value is available/)).toBeVisible()
    expect(admin.updateProviderSettings).not.toHaveBeenCalled()
  })

  it('prevents secret writes with an unsafe server key while allowing a saved value to be removed', async () => {
    vi.mocked(admin.providerSettings).mockResolvedValue([{ ...pluggy, can_store_secrets: false }])
    const { user } = renderWithProviders(<ProviderSettings />)
    await user.click(await screen.findByRole('button', { name: 'Manage Pluggy' }))
    const form = screen.getByRole('form', { name: 'Pluggy' })
    expect(within(form).getByRole('alert')).toHaveTextContent('SECRET_KEY')
    expect(within(form).getByLabelText('Client secret')).toBeDisabled()
    expect(within(form).getByRole('button', { name: 'Save changes' })).toBeDisabled()
    await user.click(within(form).getByRole('button', { name: 'Use environment value' }))
    await user.click(within(screen.getByRole('dialog', { name: 'Remove saved Client secret?' })).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(admin.updateProviderSettings).toHaveBeenCalledWith('pluggy', {
      pluggy_client_secret: null,
    }))
  })

  it('sends a boolean when the SimpleFIN switch changes', async () => {
    const simplefin: ProviderSettingStatus = {
      name: 'simplefin', configured: true, can_store_secrets: false,
      fields: { simplefin_enabled: { configured: true, source: 'environment', invalid: false, environment_configured: true } },
    }
    vi.mocked(admin.providerSettings).mockResolvedValue([simplefin])
    vi.mocked(admin.updateProviderSettings).mockResolvedValue(simplefin)
    const { user } = renderWithProviders(<ProviderSettings />)
    await user.click(await screen.findByRole('button', { name: 'Manage SimpleFIN' }))
    const form = screen.getByRole('form', { name: 'SimpleFIN' })
    expect(within(form).getByRole('button', { name: 'Save changes' })).toBeDisabled()
    const toggle = within(form).getByRole('switch', { name: 'Allow SimpleFIN connections' })
    expect(toggle).toBeChecked()
    await user.click(toggle)
    expect(toggle).not.toBeChecked()
    await user.click(within(form).getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(admin.updateProviderSettings).toHaveBeenCalledWith('simplefin', {
      simplefin_enabled: false,
    }))
  })

  it('can retry a failed provider status request', async () => {
    vi.mocked(admin.providerSettings).mockRejectedValueOnce(new Error('Offline'))
    const { user } = renderWithProviders(<ProviderSettings />)
    await screen.findByRole('alert')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('button', { name: 'Manage Pluggy' })).toBeVisible()
  })
})
