import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { useQuery } from '@tanstack/react-query'
import type { InternalAxiosRequestConfig } from 'axios'

import AccountsPage from '@/pages/accounts'
import { api } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'
import type { Account } from '@/types'

// Keep the page, edit dialog, mutation, API serialization and interceptors real.
// Only external transport and identity/provider context are synthetic.
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
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en', useDateLocale: () => 'en-US',
}))
vi.mock('@/components/bank-connect-dialog', () => ({ BankConnectDialog: () => null }))
vi.mock('@/components/connector-select-dialog', () => ({ ConnectorSelectDialog: () => null }))
vi.mock('@/components/oauth-connect-dialog', () => ({ OAuthConnectDialog: () => null }))
vi.mock('@/components/token-connect-dialog', () => ({ TokenConnectDialog: () => null }))
vi.mock('@/components/connection-settings-dialog', () => ({ ConnectionSettingsDialog: () => null }))

function GoalCacheProbe({ loadList, loadSummary }: {
  loadList: () => Promise<unknown[]>
  loadSummary: () => Promise<unknown[]>
}) {
  useQuery({ queryKey: ['goals', 'active'], queryFn: loadList, staleTime: Infinity })
  useQuery({ queryKey: ['goals', 'summary'], queryFn: loadSummary, staleTime: Infinity })
  return null
}

function account(overrides: Partial<Account> = {}): Account {
  return {
    id: 'synthetic-account', user_id: 'synthetic-user', connection_id: null,
    external_id: null, name: 'Synthetic checking', display_name: 'Original label',
    masked_number: null, institution_name: null, institution_logo_url: null,
    type: 'checking', balance: 420.35, current_balance: 420.35,
    previous_balance: null, balance_primary: 420.35, currency: 'USD',
    balance_date: '2026-06-03', credit_limit: null, available_credit: null,
    statement_close_day: null, payment_due_day: null, next_close_date: null,
    next_due_date: null, minimum_payment: null, card_brand: null, card_level: null,
    shared_balance_group: null, is_closed: false, closed_at: null,
    exclude_from_reports: false, ...overrides,
  }
}

let stored: Account
let patches: InternalAxiosRequestConfig[]
const originalAdapter = api.defaults.adapter

beforeEach(() => {
  stored = account()
  patches = []
  api.defaults.adapter = async (config) => {
    let data: unknown
    if (config.method === 'patch' && config.url === `/accounts/${stored.id}`) {
      patches.push(config)
      data = stored
    } else if (config.method === 'get') {
      switch (config.url) {
        case '/accounts':
          data = Boolean(config.params?.include_closed) === stored.is_closed ? [stored] : []
          break
        case '/connections':
          data = stored.connection_id ? [{
            id: stored.connection_id, provider: 'simplefin', status: 'active',
            institution_name: 'Synthetic bank', institutions: [],
          }] : []
          break
        case '/connections/providers':
          data = { providers: [] }
          break
        case '/assets':
        case '/currencies':
          data = []
          break
        default:
          throw new Error(`Unexpected test request: ${config.method} ${config.url}`)
      }
    } else {
      throw new Error(`Unexpected test request: ${config.method} ${config.url}`)
    }
    return { data, status: 200, statusText: 'OK', headers: {}, config }
  }
})

afterEach(() => {
  api.defaults.adapter = originalAdapter
})

async function openEditor() {
  const view = renderWithProviders(<AccountsPage />, { route: '/accounts' })
  await view.user.click(await screen.findByRole('button', { name: t('common.edit') }))
  const dialog = within(screen.getByRole('dialog', { name: t('accounts.editAccount') }))
  return { ...view, dialog }
}

async function patchBody() {
  await waitFor(() => expect(patches).toHaveLength(1))
  expect(patches[0].baseURL).toBe('/api')
  expect(patches[0].url).toBe(`/accounts/${stored.id}`)
  expect(patches[0].method).toBe('patch')
  return JSON.parse(patches[0].data as string) as Record<string, unknown>
}

describe('account edit payload', () => {
  it.each([
    { kind: 'manual', overrides: {} },
    { kind: 'connected', overrides: { connection_id: 'synthetic-connection' } },
    { kind: 'loan', overrides: {
      type: 'loan', loan_kind: 'personal', loan_original_amount: 1000,
      loan_first_payment_date: '2026-06-03', loan_term_months: 12,
    } },
    { kind: 'card', overrides: {
      type: 'credit_card', credit_limit: 5000, statement_close_day: 10, payment_due_day: 20,
    } },
  ] as { kind: string; overrides: Partial<Account> }[])(
    'sends the ordinary account save plus a changed preference for $kind accounts', async ({ overrides }) => {
      stored = account(overrides)
      const { user, dialog } = await openEditor()
      await user.click(dialog.getByRole('checkbox', { name: t('accounts.includeInReports') }))
      await user.click(dialog.getByRole('button', { name: t('common.save') }))
      const body = await patchBody()
      // The preference rides along; it does not turn the save into a flag-only PATCH.
      expect(body.exclude_from_reports).toBe(true)
      expect(body.display_name).toBe('Original label')
      expect(body.type).toBe(stored.type)
      if (stored.connection_id) {
        expect(body).not.toHaveProperty('name')
        expect(body).not.toHaveProperty('balance')
        expect(body).not.toHaveProperty('currency')
      } else {
        expect(body.name).toBe('Synthetic checking')
        expect(body.balance).toBe(420.35)
        expect(body.currency).toBe('USD')
        // Unchanged opening balance date keeps main's protection.
        expect(body).not.toHaveProperty('balance_date')
      }
      if (stored.type === 'loan') {
        expect(body).toMatchObject({
          secured_asset_id: null, loan_kind: 'personal',
          loan_original_amount: 1000, loan_term_months: 12,
        })
      }
      if (stored.type === 'credit_card') {
        expect(body).toMatchObject({
          credit_limit: 5000, statement_close_day: 10, payment_due_day: 20,
        })
      }
    },
  )

  it('keeps a closed account out of the edit UI entirely', async () => {
    stored = account({ is_closed: true, closed_at: '2026-07-01T00:00:00Z' })
    renderWithProviders(<AccountsPage />, { route: '/accounts' })
    const row = (await screen.findByText('Original label')).parentElement!.parentElement!
    expect(within(row).getByRole('button', { name: t('accounts.reopen') })).toBeEnabled()
    expect(within(row).queryByRole('button', { name: t('common.edit') })).not.toBeInTheDocument()
    expect(patches).toHaveLength(0)
  })

  it.each([false, true])('omits an untouched flag after unrelated edit (toggle restored=%s)', async (restore) => {
    stored = account({ connection_id: 'synthetic-connection' })
    const { user, dialog } = await openEditor()
    if (restore) {
      const checkbox = dialog.getByRole('checkbox', { name: t('accounts.includeInReports') })
      await user.click(checkbox)
      await user.click(checkbox)
    }
    stored = { ...stored, exclude_from_reports: true }
    const label = dialog.getByDisplayValue('Original label')
    await user.clear(label)
    await user.type(label, 'New label')
    await user.click(dialog.getByRole('button', { name: t('common.save') }))
    const body = await patchBody()
    expect(body.display_name).toBe('New label')
    expect(body).not.toHaveProperty('exclude_from_reports')
  })

  it('preserves the existing unchanged-opening-balance-date protection', async () => {
    const { user, dialog } = await openEditor()
    await user.click(dialog.getByRole('checkbox', { name: t('accounts.includeInReports') }))
    await user.click(dialog.getByRole('button', { name: t('common.save') }))
    expect(await patchBody()).not.toHaveProperty('balance_date')
  })

  it('refreshes goal caches after an open account save', async () => {
    stored = account()
    const loadList = vi.fn().mockResolvedValue([])
    const loadSummary = vi.fn().mockResolvedValue([])
    const view = renderWithProviders(
      <><AccountsPage /><GoalCacheProbe loadList={loadList} loadSummary={loadSummary} /></>,
      { route: '/accounts' },
    )
    await waitFor(() => {
      expect(loadList).toHaveBeenCalledTimes(1)
      expect(loadSummary).toHaveBeenCalledTimes(1)
    })
    await view.user.click(await screen.findByRole('button', { name: t('common.edit') }))
    const dialog = within(screen.getByRole('dialog', { name: t('accounts.editAccount') }))
    await view.user.click(dialog.getByRole('checkbox', { name: t('accounts.includeInReports') }))
    await view.user.click(dialog.getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(patches).toHaveLength(1))
    await waitFor(() => {
      expect(loadList).toHaveBeenCalledTimes(2)
      expect(loadSummary).toHaveBeenCalledTimes(2)
    })
  })
})
