import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import { admin } from '@/lib/api'
import { renderWithProviders } from '@/test/utils'

import AdminSettingsPage from './settings'

vi.mock('@/lib/api', () => ({
  admin: {
    instanceMode: vi.fn(),
    listUsers: vi.fn(),
    registrationStatus: vi.fn(),
    getSetting: vi.fn(),
    updateSetting: vi.fn(),
    deleteSetting: vi.fn(),
    timezone: vi.fn(),
    providerSettings: vi.fn(),
    updateProviderSettings: vi.fn(),
    accountingMode: vi.fn(),
    numberFormat: vi.fn(),
    dateFormat: vi.fn(),
    defaultColors: vi.fn(),
    createUser: vi.fn(),
    updateUser: vi.fn(),
    deleteUser: vi.fn(),
    emailState: vi.fn(),
    updateEmailTemplates: vi.fn(),
  },
  currencies: { list: vi.fn() },
  auth: { oidcConfig: vi.fn() },
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({
    user: { id: 'admin-1', email: 'admin@example.com', preferences: { currency_display: 'USD' } },
  }),
}))

const managedMode = {
  managed: true,
  locked_settings: ['registration_enabled', 'timezone'],
  locked_providers: true,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(admin.instanceMode).mockResolvedValue(managedMode)
  vi.mocked(admin.listUsers).mockResolvedValue({ items: [], total: 0 })
  vi.mocked(admin.registrationStatus).mockResolvedValue({ enabled: true })
  vi.mocked(admin.getSetting).mockRejectedValue(new Error('not saved'))
  vi.mocked(admin.timezone).mockResolvedValue({
    timezone: 'America/Sao_Paulo',
    saved: null,
    fallback: 'UTC',
    available: ['UTC', 'America/Sao_Paulo'],
  })
  vi.mocked(admin.providerSettings).mockResolvedValue([
    {
      name: 'pluggy',
      configured: true,
      can_store_secrets: true,
      fields: {},
    },
  ])
  vi.mocked(admin.emailState).mockResolvedValue({
    enabled: true,
    provider: 'smtp',
    sender: 'Securo <no-reply@example.com>',
    templates: {
      en: { subject: 'Reset your password', body: 'Hi,\n\n{link}\n' },
      pt: { subject: 'Redefinir sua senha', body: 'Olá,\n\n{link}\n' },
    },
  })
})

describe('admin settings on a managed instance', () => {
  it('says the instance is managed and leaves the instance settings read-only', async () => {
    renderWithProviders(<AdminSettingsPage />)

    const notice = await screen.findByRole('status')
    expect(within(notice).getByText('This instance is managed by Securo')).toBeVisible()

    // The control is there to show the state, not to change it.
    const registration = await screen.findByRole('button', { name: 'Allow user registration' })
    expect(registration).toBeDisabled()

    // Providers show who configured them instead of the dialog button.
    const providers = await screen.findByRole('region', { name: 'Bank connections' })
    expect(await within(providers).findByText('Configured by Securo')).toBeVisible()
    expect(within(providers).queryByRole('button', { name: 'Manage Pluggy' })).not.toBeInTheDocument()

    // The timezone is the operator's: the value shows, the picker does not.
    expect(await screen.findByTestId('timezone-managed')).toHaveTextContent('America/Sao_Paulo')

    // The recovery templates go out from the operator's sender to every user
    // of the instance, so their text is read-only too.
    const email = await screen.findByRole('region', { name: 'Email' })
    expect(await within(email).findByTestId('email-managed')).toHaveTextContent('Configured by Securo')
    expect(within(email).getAllByLabelText('Subject')[0]).toBeDisabled()
    expect(within(email).queryByRole('button', { name: 'Save templates' })).not.toBeInTheDocument()
  })

  it('keeps every control on a self-hosted instance', async () => {
    vi.mocked(admin.instanceMode).mockResolvedValue({
      managed: false,
      locked_settings: [],
      locked_providers: false,
    })

    renderWithProviders(<AdminSettingsPage />)

    const registration = await screen.findByRole('button', { name: 'Allow user registration' })
    expect(registration).toBeEnabled()
    const providers = await screen.findByRole('region', { name: 'Bank connections' })
    expect(await within(providers).findByRole('button', { name: 'Manage Pluggy' })).toBeVisible()
    expect(screen.queryByText('This instance is managed by Securo')).not.toBeInTheDocument()

    // The email templates stay editable on an instance the admin runs.
    const email = await screen.findByRole('region', { name: 'Email' })
    expect(await within(email).findByRole('button', { name: 'Save templates' })).toBeInTheDocument()
    expect(within(email).queryByTestId('email-managed')).not.toBeInTheDocument()
  })
})
