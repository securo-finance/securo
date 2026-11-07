import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useQuery } from '@tanstack/react-query'

import AccountsPage, { AccountDialog } from '@/pages/accounts'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, BankConnection } from '@/types'

const api = vi.hoisted(() => ({
  accountsList: vi.fn(),
  connectionsList: vi.fn(),
  sync: vi.fn(),
  update: vi.fn(),
  reopen: vi.fn(),
  canWrite: true,
}))

vi.mock('@/lib/api', () => ({
  accounts: { list: api.accountsList, update: api.update, reopen: api.reopen },
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
  useWorkspace: () => ({ canWrite: api.canWrite }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
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

function GoalCacheProbe({ loadList, loadSummary }: {
  loadList: () => Promise<unknown[]>
  loadSummary: () => Promise<unknown[]>
}) {
  useQuery({ queryKey: ['goals', 'active'], queryFn: loadList, staleTime: Infinity })
  useQuery({ queryKey: ['goals', 'summary'], queryFn: loadSummary, staleTime: Infinity })
  return null
}

describe('AccountsPage closed report preferences', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.canWrite = true
    api.connectionsList.mockResolvedValue([])
    api.update.mockResolvedValue({})
    api.reopen.mockResolvedValue({})
  })

  it.each([null, 'conn-1'])('edits reporting without reopening or changing financial fields: %s', async (connectionId) => {
    const closed = {
      ...makeAccount('conn-1'), connection_id: connectionId, is_closed: true,
      closed_at: '2026-10-01T00:00:00Z', exclude_from_reports: false,
    }
    api.accountsList.mockImplementation((includeClosed: boolean) => Promise.resolve(includeClosed ? [closed] : []))
    const loadList = vi.fn().mockResolvedValue([])
    const loadSummary = vi.fn().mockResolvedValue([])
    const { user } = renderWithProviders(
      <><AccountsPage /><GoalCacheProbe loadList={loadList} loadSummary={loadSummary} /></>,
      { route: '/accounts' },
    )
    await waitFor(() => {
      expect(loadList).toHaveBeenCalledTimes(1)
      expect(loadSummary).toHaveBeenCalledTimes(1)
    })
    const row = (await screen.findByText(closed.name)).parentElement!.parentElement!
    expect(within(row).getByRole('button', { name: t('accounts.reopen') })).toBeEnabled()
    await user.click(within(row).getByRole('button', { name: t('common.edit') }))
    const dialog = screen.getByRole('dialog', { name: t('accounts.editAccount') })
    expect(within(dialog).queryByRole('textbox')).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('combobox')).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('checkbox', { name: t('accounts.includeInReports') }))
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.update).toHaveBeenCalledExactlyOnceWith(closed.id, { exclude_from_reports: true }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => {
      expect(loadList).toHaveBeenCalledTimes(2)
      expect(loadSummary).toHaveBeenCalledTimes(2)
    })
    expect(api.reopen).not.toHaveBeenCalled()
    expect(api.sync).not.toHaveBeenCalled()
    expect(closed.is_closed).toBe(true)
  })

  it('retains the separate Reopen action', async () => {
    const closed = { ...makeAccount('conn-1'), is_closed: true }
    api.accountsList.mockImplementation((includeClosed: boolean) => Promise.resolve(includeClosed ? [closed] : []))
    const { user } = renderWithProviders(<AccountsPage />, { route: '/accounts' })
    await user.click(await screen.findByRole('button', { name: t('accounts.reopen') }))
    await waitFor(() => expect(api.reopen).toHaveBeenCalledExactlyOnceWith(closed.id))
    expect(api.update).not.toHaveBeenCalled()
  })

  it('does not offer closed-account writes to viewers', async () => {
    api.canWrite = false
    const closed = { ...makeAccount('conn-1'), is_closed: true }
    api.accountsList.mockImplementation((includeClosed: boolean) => Promise.resolve(includeClosed ? [closed] : []))
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    const row = (await screen.findByText(closed.name)).parentElement!.parentElement!
    expect(within(row).queryByRole('button', { name: t('common.edit') })).not.toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: t('accounts.reopen') })).not.toBeInTheDocument()
    api.canWrite = true
  })
})

describe('AccountDialog report exclusion', () => {
  it.each(['checking', 'credit_card', 'loan'])(
    'keeps the %s form and actions inside a viewport-bounded scrollable dialog', async (type) => {
      const account = { ...makeAccount('conn-1'), type, exclude_from_reports: false }
      const onSave = vi.fn()
      const onClose = vi.fn()
      const { user, rerender } = renderWithProviders(
        <AccountDialog open account={account} onClose={onClose} onSave={onSave} loading={false} />,
      )
      const dialog = screen.getByRole('dialog', { name: t('accounts.editAccount') })
      expect(dialog).toHaveClass('max-h-[90vh]', 'overflow-y-auto')
      const checkbox = screen.getByRole('checkbox', { name: t('accounts.includeInReports') })
      await user.click(checkbox)
      rerender(<AccountDialog open account={account} onClose={onClose} onSave={onSave} loading />)
      expect(checkbox).not.toBeChecked()
      expect(screen.getByRole('button', { name: t('common.loading') })).toBeDisabled()
      rerender(<AccountDialog open account={account} onClose={onClose} onSave={onSave} loading={false} />)
      const save = screen.getByRole('button', { name: t('common.save') })
      const close = screen.getByRole('button', { name: 'Close' })
      expect(dialog).toContainElement(checkbox)
      expect(dialog).toContainElement(save)
      expect(dialog).toContainElement(close)
      save.focus()
      await user.keyboard('{Enter}')
      expect(onSave).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ exclude_from_reports: true }))
      close.focus()
      await user.keyboard('{Enter}')
      expect(onClose).toHaveBeenCalledOnce()
    },
  )

  it.each([
    { connectionId: null, excluded: false },
    { connectionId: null, excluded: true },
    { connectionId: 'conn-1', excluded: false },
    { connectionId: 'conn-1', excluded: true },
  ])('round-trips the positive checkbox: %j', async ({ connectionId, excluded }) => {
    const account = {
      ...makeAccount('conn-1'), connection_id: connectionId, exclude_from_reports: excluded,
    }
    const onSave = vi.fn()
    const { user } = renderWithProviders(
      <AccountDialog open account={account} onClose={vi.fn()} onSave={onSave} loading={false} />,
    )
    const checkbox = screen.getByRole('checkbox', { name: t('accounts.includeInReports') })
    expect(checkbox).toHaveProperty('checked', !excluded)
    await user.click(checkbox)
    await user.click(screen.getByRole('button', { name: t('common.save') }))
    expect(onSave).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ exclude_from_reports: !excluded }),
    )
  })

  it('does not offer an exclusion override when creating an account', () => {
    renderWithProviders(
      <AccountDialog open account={null} onClose={vi.fn()} onSave={vi.fn()} loading={false} />,
    )
    expect(screen.queryByRole('checkbox', { name: t('accounts.includeInReports') }))
      .not.toBeInTheDocument()
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
