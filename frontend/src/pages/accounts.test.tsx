import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import AccountsPage, { AccountDialog } from '@/pages/accounts'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, BankConnection } from '@/types'

const api = vi.hoisted(() => ({
  accountsList: vi.fn(),
  connectionsList: vi.fn(),
  sync: vi.fn(),
  deleteConnection: vi.fn(),
  pluginsInstalled: vi.fn(),
  pluginConnections: vi.fn(),
  pluginUpdateValues: vi.fn(),
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
  accounts: { list: api.accountsList },
  assets: { list: vi.fn().mockResolvedValue([]) },
  connections: {
    list: api.connectionsList,
    getProviders: vi.fn().mockResolvedValue([]),
    sync: api.sync,
    delete: api.deleteConnection,
    getReauthUrl: vi.fn(),
  },
  currencies: { list: vi.fn().mockResolvedValue([]) },
  info: { get: vi.fn().mockResolvedValue({ features: { plugins: true } }) },
  plugins: {
    installed: api.pluginsInstalled,
    connections: api.pluginConnections,
    updateValues: api.pluginUpdateValues,
    catalog: api.pluginCatalog,
  },
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

vi.mock('@/hooks/use-timezone', () => ({
  useEffectiveTimezone: () => 'UTC',
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

vi.mock('@/components/bank-connect-dialog', () => ({
  BankConnectDialog: ({ open, reconnectConnectionId }: { open: boolean; reconnectConnectionId?: string }) =>
    open ? <div data-testid="reconnect-dialog" data-connection-id={reconnectConnectionId} /> : null,
}))
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

  it('warns instead of reporting success when only some accounts could be synced', async () => {
    api.connectionsList.mockResolvedValue([makeConnection({ id: 'conn-ok', status: 'active' })])
    api.sync.mockResolvedValue({ sync_outcome: 'partial', merged_count: 0 })
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    await user.click(await screen.findByRole('button', { name: t('accounts.sync') }))

    await vi.waitFor(() => expect(toast.warning).toHaveBeenCalledWith(t('accounts.syncPartial')))
    expect(toast.success).not.toHaveBeenCalled()
  })
})

function syncError(status: number, detail: unknown) {
  return Object.assign(new Error('Request failed'), {
    isAxiosError: true,
    response: { status, data: { detail } },
  })
}

describe('AccountsPage last sync failure', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.accountsList.mockImplementation((includeClosed: boolean) =>
      Promise.resolve(includeClosed ? [] : [makeAccount('conn-1')]),
    )
    api.sync.mockResolvedValue({ sync_outcome: 'refreshed', merged_count: 0 })
    api.pluginsInstalled.mockResolvedValue([])
    api.pluginConnections.mockResolvedValue([])
  })

  it('says why the last sync failed and offers a sync when the bank wants the user there', async () => {
    api.connectionsList.mockResolvedValue([
      makeConnection({
        id: 'conn-psu',
        status: 'error',
        last_sync_failure: { code: 'psu_present_required', at: '2026-11-18T09:30:00Z', detail: 'psu_present_required' },
      }),
    ])
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    const line = await screen.findByTestId('sync-failure')
    expect(line).toHaveTextContent(t('accounts.syncFailure.psu_present_required'))
    await user.click(within(line).getByRole('button', { name: t('accounts.syncFailure.syncNow') }))

    expect(api.sync).toHaveBeenCalledWith('conn-psu')
  })

  it('reconnects that connection when the consent expired', async () => {
    api.connectionsList.mockResolvedValue([
      makeConnection({
        id: 'conn-exp',
        provider: 'pluggy',
        status: 'expired',
        last_sync_failure: { code: 'consent_expired', at: '2026-11-18T09:30:00Z', detail: null },
      }),
    ])
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    const line = await screen.findByTestId('sync-failure')
    expect(line).toHaveTextContent(t('accounts.syncFailure.consent_expired'))
    expect(screen.queryByTestId('reconnect-dialog')).not.toBeInTheDocument()
    await user.click(within(line).getByRole('button', { name: t('accounts.reconnect') }))

    expect(await screen.findByTestId('reconnect-dialog')).toHaveAttribute('data-connection-id', 'conn-exp')
    expect(api.sync).not.toHaveBeenCalled()
  })

  it('sends the user to the SimpleFIN Bridge and offers a sync afterwards', async () => {
    api.connectionsList.mockResolvedValue([
      makeConnection({
        id: 'conn-sf',
        status: 'error',
        last_sync_failure: { code: 'reauthorize_at_provider', at: '2026-11-18T09:30:00Z', detail: 'action_required' },
      }),
    ])
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    const line = await screen.findByTestId('sync-failure')
    expect(line).toHaveTextContent(t('accounts.syncFailure.reauthorize_at_provider'))
    await user.click(within(line).getByRole('button', { name: t('accounts.syncFailure.syncNow') }))

    expect(api.sync).toHaveBeenCalledWith('conn-sf')
  })

  it('only asks to wait when the bank throttled the sync', async () => {
    api.connectionsList.mockResolvedValue([
      makeConnection({
        last_sync_failure: { code: 'rate_limited', at: '2026-11-18T09:30:00Z', detail: null },
      }),
    ])
    renderWithProviders(<AccountsPage />, { route: '/accounts' })

    const line = await screen.findByTestId('sync-failure')
    expect(line).toHaveTextContent(t('accounts.syncRateLimited'))
    expect(within(line).queryByRole('button')).not.toBeInTheDocument()
  })

  it('drops the notice once a sync succeeded', async () => {
    const failed = makeConnection({
      id: 'conn-1',
      status: 'error',
      last_sync_failure: { code: 'provider_error', at: '2026-11-18T09:30:00Z', detail: 'HTTP 502' },
    })
    api.connectionsList
      .mockResolvedValueOnce([failed])
      .mockResolvedValue([{ ...failed, status: 'active', last_sync_failure: null }])
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    const line = await screen.findByTestId('sync-failure')
    await user.click(within(line).getByRole('button', { name: t('accounts.syncFailure.syncNow') }))

    expect(api.sync).toHaveBeenCalledWith('conn-1')
    await vi.waitFor(() => expect(screen.queryByTestId('sync-failure')).not.toBeInTheDocument())
  })

  it.each([
    [
      409,
      { message: 'Bank wants the PSU present', code: 'psu_present_required' },
      'accounts.syncFailure.psu_present_required',
    ],
    [409, { message: 'Token revoked', code: 'credentials_invalid' }, 'accounts.syncFailure.consent_expired'],
    [409, { message: 'Bridge wants it reauthorized', code: 'some_new_code' }, 'accounts.syncFailure.provider_error'],
    [409, 'Connection was disconnected', 'accounts.connectionDisconnected'],
    [410, 'Session expired', 'accounts.syncFailure.consent_expired'],
    [500, 'Sync failed: HTTPStatusError 502 for IBAN-123', 'accounts.syncFailure.provider_error'],
  ])('shows the translated reason in the manual sync toast (%i)', async (status, detail, key) => {
    api.connectionsList.mockResolvedValue([makeConnection({ id: 'conn-ok', status: 'active' })])
    api.sync.mockRejectedValue(syncError(status, detail))
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    await user.click(await screen.findByRole('button', { name: t('accounts.sync') }))

    await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith(t(key)))
  })

  it('keeps the server text when the provider is not configured', async () => {
    api.connectionsList.mockResolvedValue([makeConnection({ id: 'conn-ok', status: 'active' })])
    api.sync.mockRejectedValue(syncError(503, "Provider 'x' is not configured."))
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    await user.click(await screen.findByRole('button', { name: t('accounts.sync') }))

    await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith("Provider 'x' is not configured."))
  })
})

describe('AccountsPage disconnect dialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.accountsList.mockResolvedValue([])
    api.deleteConnection.mockResolvedValue(undefined)
  })

  it('keeps accounts and history unless the delete box is checked', async () => {
    api.connectionsList.mockResolvedValue([makeConnection({ id: 'conn-d', status: 'active' })])
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    await user.click(await screen.findByTitle(t('accounts.disconnect')))
    const box = screen.getByRole('checkbox', { name: t('accounts.disconnectAlsoDelete') })
    expect(box).not.toBeChecked()
    expect(screen.getByTestId('disconnect-description')).toHaveTextContent(
      t('accounts.confirmDisconnectKeepDesc', { institution: 'Institution B' }),
    )
    await user.click(screen.getByRole('button', { name: t('accounts.disconnect') }))

    expect(api.deleteConnection).toHaveBeenCalledExactlyOnceWith('conn-d', { keepAccounts: true })
  })

  it('deletes accounts and transactions when the box is checked', async () => {
    api.connectionsList.mockResolvedValue([makeConnection({ id: 'conn-d', status: 'active' })])
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    await user.click(await screen.findByTitle(t('accounts.disconnect')))
    await user.click(screen.getByRole('checkbox', { name: t('accounts.disconnectAlsoDelete') }))
    expect(screen.getByTestId('disconnect-description')).toHaveTextContent(
      t('accounts.confirmDisconnectDesc', { institution: 'Institution B' }),
    )
    await user.click(screen.getByRole('button', { name: t('accounts.disconnect') }))

    expect(api.deleteConnection).toHaveBeenCalledExactlyOnceWith('conn-d', { keepAccounts: false })
  })

  it('offers reconnect and removal for a disconnected connection', async () => {
    api.connectionsList.mockResolvedValue([makeConnection({ id: 'conn-d', status: 'disconnected' })])
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })

    expect(await screen.findByText(t('accounts.connectionStatus.disconnected'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('accounts.reconnect') })).toBeInTheDocument()
    await user.click(screen.getByTitle(t('accounts.removeConnection')))
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('accounts.removeConnection') }))

    expect(api.deleteConnection).toHaveBeenCalledExactlyOnceWith('conn-d', { keepAccounts: false })
  })
})

describe('AccountDialog account types', () => {
  it.each([
    { kind: 'manual', connectionId: null },
    { kind: 'bank-connected', connectionId: 'conn-d' },
  ])('saves Loan when editing an existing $kind checking account', async ({ connectionId }) => {
    const account = { ...makeAccount('conn-d'), connection_id: connectionId }
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
    integrations: [
      {
        key: 'keys',
        name: { en: 'Ledger (API key)' },
        auth: 'credentials' as const,
        fields: [{ key: 'api_key', label: { en: 'API key' }, secret: true, required: true }],
        sync_minutes: 60,
        provides: ['accounts' as const],
      },
    ],
    network: [],
    api_scopes: [],
    redirect_uri: null,
  }
  const pluginConnection = {
    id: 'pc-1',
    plugin_id: 'acme/ledger',
    integration: 'keys',
    name: 'Main',
    status: 'needs_auth' as const,
    config: {},
    secret_fields: ['api_key'],
    sync_minutes: 60,
    last_sync_at: null,
    last_error: 'NeedsAuth: sign in',
    created_at: '2026-11-12T00:00:00Z',
  }

  beforeEach(() => {
    vi.clearAllMocks()
    api.accountsList.mockResolvedValue([])
    api.pluginsInstalled.mockResolvedValue([plugin])
    api.pluginConnections.mockResolvedValue([pluginConnection])
    api.pluginCatalog.mockResolvedValue([])
    api.connectionsList.mockResolvedValue([
      makeConnection({
        id: 'bank-1',
        provider: 'plugin:acme/ledger',
        status: 'expired',
        display_name: 'Main',
        plugin_connection_id: 'pc-1',
        plugin_id: 'acme/ledger',
        integration: 'keys',
      }),
    ])
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

  it("shows the plugin's catalog icon on a connection without a logo", async () => {
    const icon = `/api/plugins/icons/${'b'.repeat(64)}.png`
    api.pluginCatalog.mockResolvedValue([{ id: 'acme/ledger', icon_url: icon }])
    const { container } = renderWithProviders(<AccountsPage />, { route: '/accounts' })
    await waitFor(() => expect(container.querySelector(`img[src="${icon}"]`)).not.toBeNull())
  })

  it('does not ask the catalog for icons when every plugin connection has a logo', async () => {
    api.connectionsList.mockResolvedValue([
      makeConnection({
        id: 'bank-1',
        provider: 'plugin:acme/ledger',
        status: 'active',
        display_name: 'Main',
        plugin_connection_id: 'pc-1',
        plugin_id: 'acme/ledger',
        integration: 'keys',
        logo_url: '/logo.png',
      }),
    ])
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    expect(await screen.findByText('Main')).toBeInTheDocument()
    expect(api.pluginCatalog).not.toHaveBeenCalled()
  })

  it('shows what a reconnect brought in, even when its sync settles before any poll', async () => {
    let synced = false
    api.accountsList.mockImplementation(async () =>
      synced ? [{ ...makeAccount('bank-1'), connection_id: null, name: 'Brokerage Cash', institution_name: null }] : [],
    )
    // The sync is done by the time the page asks: it never reads as pending.
    api.pluginUpdateValues.mockImplementation(async () => {
      synced = true
      api.pluginConnections.mockResolvedValue([{ ...pluginConnection, status: 'active' as const, last_error: null }])
      return { ...pluginConnection, status: 'pending' as const }
    })
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })
    await user.click(await screen.findByRole('button', { name: t('accounts.reconnect') }))
    await user.click(await screen.findByRole('button', { name: t('common.save') }))
    expect(await screen.findByText('Brokerage Cash', {}, { timeout: 5000 })).toBeInTheDocument()
  })

  it('refreshes after a reconnect whose settled state the page had already fetched', async () => {
    let synced = false
    api.accountsList.mockImplementation(async () =>
      synced ? [{ ...makeAccount('bank-1'), connection_id: null, name: 'Brokerage Cash', institution_name: null }] : [],
    )
    // Every fetch, before and after saving, returns the same settled connection.
    api.pluginConnections.mockResolvedValue([{ ...pluginConnection, status: 'active' as const, last_error: null }])
    api.pluginUpdateValues.mockImplementation(async () => {
      synced = true
      return { ...pluginConnection, status: 'pending' as const }
    })
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })
    await user.click(await screen.findByRole('button', { name: t('accounts.reconnect') }))
    await user.click(await screen.findByRole('button', { name: t('common.save') }))
    expect(await screen.findByText('Brokerage Cash', {}, { timeout: 5000 })).toBeInTheDocument()
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
      makeConnection({
        id: 'bank-1',
        provider: 'plugin:acme/ledger',
        plugin_connection_id: 'pc-1',
        plugin_id: 'acme/ledger',
      }),
      makeConnection({
        id: 'bank-2',
        provider: 'plugin:acme/ledger',
        plugin_connection_id: 'pc-2',
        plugin_id: 'acme/ledger',
      }),
    ])
    // What the first connection brought in only exists once it finished.
    let synced = false
    api.accountsList.mockImplementation(async () =>
      synced ? [{ ...makeAccount('bank-1'), connection_id: null, name: 'Brokerage Cash', institution_name: null }] : [],
    )
    api.pluginConnections.mockReset()
    api.pluginConnections.mockResolvedValueOnce([pending('pc-1'), pending('pc-2')]).mockImplementation(async () => {
      synced = true
      return [{ ...pluginConnection, id: 'pc-1', status: 'active' as const }, pending('pc-2')]
    })
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    expect(await screen.findByText('Brokerage Cash', {}, { timeout: 6000 })).toBeInTheDocument()
  }, 10000)
})
