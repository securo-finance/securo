import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
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
    { kind: 'closed', overrides: { is_closed: true, closed_at: '2026-07-01T00:00:00Z' } },
  ] as { kind: string; overrides: Partial<Account> }[])(
    'PATCHes only a changed flag for $kind accounts', async ({ overrides }) => {
      stored = account(overrides)
      const { user, dialog } = await openEditor()
      // A later provider/other-tab update must not be overwritten by an old
      // balance, type, loan term or card field in this dialog's snapshot.
      stored = { ...stored, balance: 987.65, currency: 'EUR' }
      await user.click(dialog.getByRole('checkbox', { name: t('accounts.includeInReports') }))
      await user.click(dialog.getByRole('button', { name: t('common.save') }))
      expect(await patchBody()).toEqual({ exclude_from_reports: true })
    },
  )

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

  it('omits untouched saved collateral when a flag-only loan edit loads no eligible properties', async () => {
    stored = account({
      name: 'Synthetic secured mortgage', type: 'loan', balance: 180000,
      current_balance: -180000, balance_primary: -180000,
      secured_asset_id: '10000000-0000-4000-8000-000000000008',
      loan_kind: 'mortgage', loan_original_amount: 225000,
      loan_interest_rate: 4.75, loan_rate_period: 'yearly_nominal',
      loan_term_months: 240, loan_first_payment_date: '2026-06-03',
      loan_amortization: 'sac',
    })
    const { user, dialog, queryClient } = await openEditor()
    // The existing adapter returns an empty eligible-asset list. Wait for
    // that response so this cannot pass while the assets query is unresolved.
    await waitFor(() => expect(queryClient.getQueryState(['assets'])?.status).toBe('success'))
    expect(queryClient.getQueryData(['assets'])).toEqual([])
    expect(dialog.getByDisplayValue('225000')).toBeInTheDocument()
    expect(dialog.getByDisplayValue('4.75')).toBeInTheDocument()

    await user.click(dialog.getByRole('checkbox', { name: t('accounts.includeInReports') }))
    await user.click(dialog.getByRole('button', { name: t('common.save') }))

    // Inspect the real serialized PATCH outside the transport callback:
    // preserving collateral here means omitting it, not resending its ID.
    expect(await patchBody()).toEqual({ exclude_from_reports: true })
  })

  it('represents saved unavailable collateral distinctly before deliberately clearing only its link', async () => {
    const savedCollateralId = '10000000-0000-4000-8000-000000000008'
    stored = account({
      name: 'Synthetic secured mortgage', type: 'loan', balance: 180000,
      current_balance: -180000, balance_primary: -180000,
      secured_asset_id: savedCollateralId,
      loan_kind: 'mortgage', loan_original_amount: 225000,
      loan_interest_rate: 4.75, loan_rate_period: 'yearly_nominal',
      loan_term_months: 240, loan_first_payment_date: '2026-06-03',
      loan_amortization: 'sac',
    })
    const { user, dialog, queryClient } = await openEditor()
    await waitFor(() => expect(queryClient.getQueryState(['assets'])?.status).toBe('success'))
    expect(queryClient.getQueryData(['assets'])).toEqual([])

    const noProperty = dialog.getByRole('option', { name: t('accounts.noSecuredProperty') })
    const collateral = noProperty.closest('select')
    if (!collateral) throw new Error('Collateral option has no select control')
    expect(noProperty).toHaveValue('')
    // A saved link must not already look cleared before the user acts.
    // Check the actual DOM value, not React state or an invented option label.
    expect(collateral).toHaveValue(savedCollateralId)
    expect(collateral.selectedOptions[0]).not.toBe(noProperty)

    await user.selectOptions(collateral, noProperty)
    expect(collateral).toHaveValue('')
    await user.click(dialog.getByRole('button', { name: t('common.save') }))
    expect(await patchBody()).toEqual({ secured_asset_id: null })
  })
})
