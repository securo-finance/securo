import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import AccountsPage, { AccountDialog } from '@/pages/accounts'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, BankConnection } from '@/types'

const api = vi.hoisted(() => ({
  accountsList: vi.fn(),
  connectionsList: vi.fn(),
  sync: vi.fn(),
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
  usePrivacyMode: () => ({ mask: false }),
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/lib/invalidate-queries', () => ({
  invalidateFinancialQueries: vi.fn(),
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
