import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import AccountsPage, { AccountDialog } from '@/pages/accounts'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, BankConnection } from '@/types'

const api = vi.hoisted(() => ({
  accountsList: vi.fn(),
  connectionsList: vi.fn(),
  sync: vi.fn(),
  pluginsInstalled: vi.fn(),
  pluginConnections: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  accounts: { list: api.accountsList },
  assets: { list: vi.fn().mockResolvedValue([]) },
  connections: {
    list: api.connectionsList,
    getProviders: vi.fn().mockResolvedValue([]),
    sync: api.sync,
    delete: vi.fn(),
    getReauthUrl: vi.fn(),
  },
  currencies: { list: vi.fn().mockResolvedValue([]) },
  info: { get: vi.fn().mockResolvedValue({ features: { plugins: true } }) },
  plugins: { installed: api.pluginsInstalled, connections: api.pluginConnections },
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
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
vi.mock('@/components/account-page-actions', () => ({ AccountPageActions: () => null }))
vi.mock('@/components/account-row-actions', () => ({ AccountRowActions: () => null }))

function makeConnection(overrides: Partial<BankConnection>): BankConnection {
  return {
    id: 'conn-1',
    user_id: 'user-1',
    provider: 'simplefin',
    institution_name: 'Institution B',
    display_name: null,
    logo_url: null,
    external_id: 'INST-B',
    status: 'active',
    settings: null,
    last_sync_at: null,
    created_at: '2026-10-01T00:00:00Z',
    institutions: [],
    ...overrides,
  }
}

function makeAccount(connectionId: string): Account {
  return {
    id: 'acc-1',
    user_id: 'user-1',
    connection_id: connectionId,
    external_id: 'acc-external-1',
    name: 'Healthy Checking',
    display_name: null,
    masked_number: null,
    institution_name: 'Institution B',
    institution_logo_url: null,
    type: 'checking',
    balance: 42,
    current_balance: 42,
    previous_balance: null,
    balance_primary: 42,
    currency: 'USD',
    credit_limit: null,
    available_credit: null,
    statement_close_day: null,
    payment_due_day: null,
    is_closed: false,
  } as unknown as Account
}

describe('AccountsPage connection row actions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.accountsList.mockImplementation((includeClosed: boolean) =>
      Promise.resolve(includeClosed ? [] : [makeAccount('conn-1')]),
    )
    api.connectionsList.mockResolvedValue([])
    api.sync.mockResolvedValue({ sync_outcome: 'success', merged_count: 0 })
    api.pluginsInstalled.mockResolvedValue([])
    api.pluginConnections.mockResolvedValue([])
  })

  it('offers a retry next to reconnect for a connection in the error state', async () => {
    api.connectionsList.mockResolvedValue([makeConnection({ id: 'conn-err', status: 'error' })])
    renderWithProviders(<AccountsPage />, { route: '/accounts' })

    await screen.findByRole('button', { name: t('accounts.retrySync') })
    expect(screen.getByRole('button', { name: t('accounts.reconnect') })).toBeInTheDocument()
  })

  it('keeps reconnect as the only action for an expired connection', async () => {
    api.connectionsList.mockResolvedValue([makeConnection({ id: 'conn-exp', status: 'expired' })])
    renderWithProviders(<AccountsPage />, { route: '/accounts' })

    await screen.findByRole('button', { name: t('accounts.reconnect') })
    expect(screen.queryByRole('button', { name: t('accounts.retrySync') })).not.toBeInTheDocument()
  })

  it('keeps sync as the only action for an active connection', async () => {
    api.connectionsList.mockResolvedValue([makeConnection({ id: 'conn-ok', status: 'active' })])
    renderWithProviders(<AccountsPage />, { route: '/accounts' })

    await screen.findByRole('button', { name: t('accounts.sync') })
    expect(screen.queryByRole('button', { name: t('accounts.reconnect') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('accounts.retrySync') })).not.toBeInTheDocument()
  })

  it('retries the sync for the connection whose row the retry button sits in', async () => {
    api.connectionsList.mockResolvedValue([makeConnection({ id: 'conn-err', status: 'error' })])
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    const retry = await screen.findByRole('button', { name: t('accounts.retrySync') })
    await user.click(retry)

    expect(api.sync).toHaveBeenCalledWith('conn-err')
  })
})

describe('AccountDialog account types', () => {
  it.each([
    { kind: 'manual', connectionId: null },
    { kind: 'bank-connected', connectionId: 'conn-1' },
  ])('saves Loan when editing an existing $kind checking account', async ({ connectionId }) => {
    const account = { ...makeAccount('conn-1'), connection_id: connectionId }
    const onSave = vi.fn()
    const { user } = renderWithProviders(
      <AccountDialog open account={account} onClose={vi.fn()} onSave={onSave} loading={false} />,
    )

    expect(screen.getByRole('heading', { name: t('accounts.editAccount') })).toBeInTheDocument()
    const [accountType] = screen.getAllByRole('combobox')
    expect(accountType).toHaveValue('checking')
    await user.selectOptions(accountType, 'loan')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    expect(onSave).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ type: 'loan' }))
  })
})

describe('AccountsPage connections a plugin syncs', () => {
  const plugin = {
    plugin_id: 'acme/ledger',
    name: { en: 'Ledger' },
    version: '1.0.0',
    commit: '1'.repeat(40),
    installed_at: '2026-11-12T00:00:00Z',
    integrations: [{
      key: 'keys',
      name: { en: 'Ledger (API key)' },
      auth: 'credentials' as const,
      fields: [{ key: 'api_key', label: { en: 'API key' }, secret: true, required: true }],
      sync_minutes: 60,
      provides: ['accounts' as const],
    }],
    network: [],
    api_scopes: [],
    redirect_uri: null,
  }
  const pluginConnection = {
    id: 'pc-1', plugin_id: 'acme/ledger', integration: 'keys', name: 'Main', status: 'needs_auth' as const,
    config: {}, secret_fields: ['api_key'], sync_minutes: 60, last_sync_at: null, last_error: 'NeedsAuth: sign in',
    created_at: '2026-11-12T00:00:00Z',
  }

  beforeEach(() => {
    vi.clearAllMocks()
    api.accountsList.mockResolvedValue([])
    api.pluginsInstalled.mockResolvedValue([plugin])
    api.pluginConnections.mockResolvedValue([pluginConnection])
    api.connectionsList.mockResolvedValue([makeConnection({
      id: 'bank-1', provider: 'plugin:acme/ledger', status: 'expired', display_name: 'Main',
      plugin_connection_id: 'pc-1', plugin_id: 'acme/ledger', integration: 'keys',
    })])
  })

  it('shows the plugin and reconnects through its form', async () => {
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })
    expect(await screen.findByText('Main')).toBeInTheDocument()
    expect(await screen.findByText('LE')).toBeInTheDocument()

    await user.click(await screen.findByRole('button', { name: t('accounts.reconnect') }))
    expect(await screen.findByText(t('plugins.connections.editTitle', { name: 'Main' }))).toBeInTheDocument()
    expect(screen.getByText(t('plugins.connections.keepBlank'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('common.save') })).toBeEnabled()
  })

  it('reconnects even before the page has loaded the plugin connections', async () => {
    api.pluginConnections.mockResolvedValueOnce([]).mockResolvedValue([pluginConnection])
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })
    await user.click(await screen.findByRole('button', { name: t('accounts.reconnect') }))
    expect(await screen.findByText(t('plugins.connections.editTitle', { name: 'Main' }))).toBeInTheDocument()
  })

  it('refreshes the page when one connection finishes while another still syncs', async () => {
    const pending = (id: string) => ({ ...pluginConnection, id, status: 'pending' as const })
    api.connectionsList.mockResolvedValue([
      makeConnection({ id: 'bank-1', provider: 'plugin:acme/ledger', plugin_connection_id: 'pc-1', plugin_id: 'acme/ledger' }),
      makeConnection({ id: 'bank-2', provider: 'plugin:acme/ledger', plugin_connection_id: 'pc-2', plugin_id: 'acme/ledger' }),
    ])
    // What the first connection brought in only exists once it finished.
    let synced = false
    api.accountsList.mockImplementation(async () =>
      synced ? [{ ...makeAccount('bank-1'), connection_id: null, name: 'Brokerage Cash', institution_name: null }] : [],
    )
    api.pluginConnections.mockReset()
    api.pluginConnections
      .mockResolvedValueOnce([pending('pc-1'), pending('pc-2')])
      .mockImplementation(async () => {
        synced = true
        return [{ ...pluginConnection, id: 'pc-1', status: 'active' as const }, pending('pc-2')]
      })
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    expect(await screen.findByText('Brokerage Cash', {}, { timeout: 6000 })).toBeInTheDocument()
  }, 10000)
})
