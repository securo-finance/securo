import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, screen, waitFor } from '@testing-library/react'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import AccountDetailPage from '@/pages/account-detail'
import { MobileTransactionRow } from '@/components/mobile-transaction-row'
import { i18n, renderWithProviders } from '@/test/utils'
import type { Transaction } from '@/types'

type AsgiFixture = {
  account: { id: string; name: string; type: string; currency: string; balance: string | number }
  responses: Record<string, { items: Transaction[]; total: number; [key: string]: unknown }>
}

const asgiFixturePath = process.env.SECURO_1022_ASGI_FIXTURE
const asgiFixture = asgiFixturePath
  ? JSON.parse(readFileSync(asgiFixturePath, 'utf8')) as AsgiFixture
  : null

const api = vi.hoisted(() => ({
  accounts: { get: vi.fn(), bills: vi.fn(), summary: vi.fn(), list: vi.fn(), update: vi.fn() },
  transactions: { list: vi.fn(), update: vi.fn(), delete: vi.fn(), create: vi.fn() },
  dashboard: { projectedTransactions: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
}))
const viewport = vi.hoisted(() => ({ mobile: false }))

vi.mock('@/lib/api', () => ({
  accounts: api.accounts,
  transactions: api.transactions,
  dashboard: api.dashboard,
  categories: api.categories,
  categoryGroups: api.categoryGroups,
}))
vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => viewport.mobile }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '***' }),
}))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'BRL' } } }),
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
}))

const account = {
  id: asgiFixture?.account.id ?? 'acc-1',
  name: asgiFixture?.account.name ?? 'Checking',
  type: asgiFixture?.account.type ?? 'checking',
  currency: asgiFixture?.account.currency ?? 'BRL',
  balance: Number(asgiFixture?.account.balance ?? 150),
  current_balance: 150, is_active: true,
}
const opening = {
  id: 'opening', user_id: 'user-1', account_id: 'acc-1', category_id: null,
  category: null, external_id: null, description: 'Saldo inicial', original_description: null,
  amount: 100, currency: 'BRL', date: '2026-09-12', type: 'credit',
  source: 'opening_balance', status: 'posted', payee: null, payee_id: null,
  payee_name: null, notes: null, transfer_pair_id: null, amount_primary: null,
  fx_rate_used: null, fx_fallback: false, installment_number: null,
  total_installments: null, installment_total_amount: null,
  installment_purchase_date: null, installment_series_id: null, bill_id: null,
  effective_bill_date: null, splits: [], is_ignored: false,
} satisfies Transaction
const manual = {
  ...opening, id: 'manual', source: 'manual', amount: 50, date: '2026-09-11',
} satisfies Transaction
const items: Transaction[] = asgiFixture?.responses.en.items ?? [opening, manual]

function captureAsgiRender(locale: string) {
  const directory = process.env.SECURO_1022_RENDERED_DOM_DIR
  if (!asgiFixture || !directory) return
  mkdirSync(directory, { recursive: true })
  const device = viewport.mobile ? 'mobile' : 'desktop'
  writeFileSync(join(directory, `${device}-${locale}.html`), document.body.innerHTML)
}

beforeEach(async () => {
  vi.clearAllMocks()
  viewport.mobile = false
  await i18n.changeLanguage('en')
  api.accounts.get.mockResolvedValue(account)
  api.accounts.list.mockResolvedValue([account])
  api.accounts.summary.mockResolvedValue({
    opening_balance: 0, current_balance: 150, monthly_income: 0, monthly_expenses: 0,
    projected_income: 0, projected_expenses: 0,
  })
  api.transactions.list.mockResolvedValue(
    asgiFixture?.responses.en ?? { items, total: items.length },
  )
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
})

function description(text: string, mobile: boolean) {
  return screen.getByText(text, {
    selector: mobile ? 'p.font-semibold' : 'span.font-semibold',
  })
}

describe.each([false, true])('account-detail opening-balance description (mobile=%s)', (mobile) => {
  it('uses only the UI locale for the synthetic description and never rewrites an ordinary row', async () => {
    viewport.mobile = mobile
    const originalItems = structuredClone(items)
    renderWithProviders(<AccountDetailPage />, {
      route: `/accounts/${account.id}`, path: '/accounts/:id',
    })
    await waitFor(() => expect(api.transactions.list).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryAllByText('Saldo inicial', {
      selector: mobile ? 'p.font-semibold' : 'span.font-semibold',
    }).length).toBeGreaterThan(0))

    // The translated badge alone must not satisfy this: check the description element.
    expect(description('opening balance', mobile)).toBeVisible()
    expect(screen.getAllByText('opening balance')).toHaveLength(1)
    expect(description('Saldo inicial', mobile)).toBeVisible() // manual row, same stored text
    expect(api.transactions.list).toHaveBeenCalledWith(expect.objectContaining({ include_opening_balance: true }))
    expect(items).toEqual(originalItems)
    captureAsgiRender('en')

    await act(async () => { await i18n.changeLanguage('pt-BR') })
    expect(screen.getAllByText('saldo inicial', {
      selector: mobile ? 'p.font-semibold' : 'span.font-semibold',
      exact: false,
    })).toHaveLength(2)
    captureAsgiRender('pt-BR')
    await act(async () => { await i18n.changeLanguage('en') })
    expect(description('opening balance', mobile)).toBeVisible()
    expect(description('Saldo inicial', mobile)).toBeVisible()
    expect(opening.description).toBe('Saldo inicial')
    expect(manual.description).toBe('Saldo inicial')
    expect(items).toEqual(originalItems) // amount, date, source and API data stay unchanged
    expect(api.transactions.update).not.toHaveBeenCalled()
    captureAsgiRender('en-restored')
  })
})

it('leaves the shared mobile row unchanged for other callers', async () => {
  renderWithProviders(
    <MobileTransactionRow
      tx={opening} account={undefined} groupName={undefined} selected={false}
      selectable={false} canWrite={false} highlighted={false} locale="en-US"
      userCurrency="BRL" onSelect={() => {}} onClick={() => {}}
    />,
  )
  expect(screen.getByText('Saldo inicial', { selector: 'p.font-semibold' })).toBeVisible()
  expect(screen.queryByText('opening balance')).not.toBeInTheDocument()
})
