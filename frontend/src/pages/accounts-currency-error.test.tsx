import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import AccountsPage, { AccountDialog } from '@/pages/accounts'
import { renderWithProviders, t } from '@/test/utils'

// A preference the instance does not support is a free value
// (`currency_display`), so the dialog has to cope with it (issue #296).
const api = vi.hoisted(() => ({
  accountsList: vi.fn(),
  accountsCreate: vi.fn(),
  connectionsList: vi.fn(),
  currenciesList: vi.fn(),
  pluginsInstalled: vi.fn(),
  pluginConnections: vi.fn(),
  pluginCatalog: vi.fn(),
}))

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  error: vi.fn(),
}))

vi.mock('sonner', () => ({ toast }))

vi.mock('@/lib/api', () => ({
  accounts: { list: api.accountsList, create: api.accountsCreate },
  assets: { list: vi.fn().mockResolvedValue([]) },
  connections: {
    list: api.connectionsList,
    getProviders: vi.fn().mockResolvedValue([]),
    getReauthUrl: vi.fn(),
  },
  currencies: { list: api.currenciesList },
  info: { get: vi.fn().mockResolvedValue({ features: { plugins: false } }) },
  plugins: {
    installed: api.pluginsInstalled,
    connections: api.pluginConnections,
    updateValues: vi.fn(),
    catalog: api.pluginCatalog,
  },
}))

const auth = vi.hoisted(() => ({ currency: 'XYZ' }))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: auth.currency } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ privacyMode: false, mask: (value: string) => value, MASK: '••••' }),
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/components/bank-connect-dialog', () => ({ BankConnectDialog: () => null }))
vi.mock('@/components/connector-select-dialog', () => ({ ConnectorSelectDialog: () => null }))
vi.mock('@/components/oauth-connect-dialog', () => ({ OAuthConnectDialog: () => null }))
vi.mock('@/components/token-connect-dialog', () => ({ TokenConnectDialog: () => null }))
vi.mock('@/components/connection-settings-dialog', () => ({ ConnectionSettingsDialog: () => null }))
vi.mock('@/components/account-row-actions', () => ({ AccountRowActions: () => null }))

const SUPPORTED = [
  { code: 'USD', symbol: '$', name: 'US Dollar', flag: '🇺🇸' },
  { code: 'EUR', symbol: '€', name: 'Euro', flag: '🇪🇺' },
]

beforeEach(() => {
  vi.clearAllMocks()
  auth.currency = 'XYZ'
  api.accountsList.mockResolvedValue([])
  api.connectionsList.mockResolvedValue([])
  api.pluginsInstalled.mockResolvedValue([])
  api.pluginConnections.mockResolvedValue([])
  api.pluginCatalog.mockResolvedValue([])
  api.currenciesList.mockResolvedValue([])
})

describe('creating an account whose currency is not supported (#296)', () => {
  it('shows the sentence the API sent instead of the generic error', async () => {
    api.accountsCreate.mockRejectedValue({
      response: { data: { detail: "unsupported currency code: 'XYZ'" } },
    })

    renderWithProviders(<AccountsPage />)

    const [addButton] = await screen.findAllByRole('button', { name: new RegExp(t('accounts.addManual')) })
    fireEvent.click(addButton)
    fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Wallet' } })
    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("unsupported currency code: 'XYZ'"),
    )
    expect(toast.error).not.toHaveBeenCalledWith(t('common.error'))
  })
})

describe('the currency the form sends for a new account (#296)', () => {
  it('falls back to a supported code when the preference is outside the allow-list', async () => {
    api.currenciesList.mockResolvedValue(SUPPORTED)
    const onSave = vi.fn()

    renderWithProviders(
      <AccountDialog open onClose={vi.fn()} account={null} onSave={onSave} loading={false} />,
    )

    fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Wallet' } })
    // Once the allowed codes arrive, the select shows a supported one.
    await screen.findByDisplayValue(/US Dollar/)
    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave.mock.calls[0][0]).toMatchObject({ currency: 'USD' })
  })

  it('keeps the preference when the instance supports it', async () => {
    auth.currency = 'BRL'
    api.currenciesList.mockResolvedValue([...SUPPORTED, { code: 'BRL', symbol: 'R$', name: 'Real', flag: '🇧🇷' }])
    const onSave = vi.fn()

    renderWithProviders(
      <AccountDialog open onClose={vi.fn()} account={null} onSave={onSave} loading={false} />,
    )

    fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Wallet' } })
    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave.mock.calls[0][0]).toMatchObject({ currency: 'BRL' })
  })
})
