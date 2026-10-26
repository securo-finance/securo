import { useState } from 'react'
import { act, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { CommandPalette } from '@/components/command-palette'
import { i18n, renderWithProviders } from '@/test/utils'
import type { SearchHit } from '@/lib/api'
import { transactionDescription } from '@/lib/transaction-description'
import { SUPPORTED_LANGS } from '@/lib/i18n'

const api = vi.hoisted(() => ({ query: vi.fn() }))
vi.mock('@/lib/api', () => ({ search: api }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => ({ hasModule: () => true }) }))
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => 'en-US' }))

const opening: SearchHit = {
  type: 'transaction', id: 'opening', label: 'Saldo inicial', subtitle: 'Synthetic',
  amount: 100, currency: 'BRL', date: '2026-09-12', icon: null, color: null,
  meta: { source: 'opening_balance', account_id: 'acc-1', tx_type: 'credit' },
}
const hits: SearchHit[] = [
  opening,
  { ...opening, id: 'manual', subtitle: 'Manual', meta: { source: 'manual' } },
  { ...opening, id: 'legacy', subtitle: 'Legacy', meta: {} },
  { ...opening, id: 'account', type: 'account', subtitle: 'Account' },
  { ...opening, id: 'null', subtitle: 'Null source', meta: { source: null } },
]

function Harness() {
  const [open, setOpen] = useState(true)
  return <><button onClick={() => setOpen(true)}>Open palette</button><CommandPalette open={open} onOpenChange={setOpen} /></>
}

beforeEach(async () => {
  vi.clearAllMocks()
  await i18n.changeLanguage('en')
  api.query.mockResolvedValue(hits)
})

it('translates only source-qualified transaction hits and keeps future recents live across locales', async () => {
  const original = structuredClone(hits)
  const { user } = renderWithProviders(<Harness />)
  await user.type(screen.getByRole('combobox'), 'saldo')
  await waitFor(() => expect(api.query).toHaveBeenCalledWith('saldo', 5))
  await waitFor(() => expect(screen.getByText('Synthetic', { exact: false })).toBeVisible())
  const option = (subtitle: string) => screen.getByRole('option', { name: new RegExp(subtitle) })
  expect(within(option('Synthetic')).getByText('Opening balance')).toBeVisible()
  for (const subtitle of ['Manual', 'Legacy', 'Account', 'Null source']) {
    expect(within(option(subtitle)).getByText('Saldo inicial')).toBeVisible()
  }
  const amount = within(option('Synthetic')).getByText(/100\.00/).textContent
  await act(async () => { await i18n.changeLanguage('pt-BR') })
  expect(within(option('Synthetic')).getByText('Saldo inicial')).toBeVisible()
  await act(async () => { await i18n.changeLanguage('nl') })
  expect(within(option('Synthetic')).getByText('Openingsbalans')).toBeVisible()
  expect(within(option('Synthetic')).getByText(/100\.00/).textContent).toBe(amount)
  await user.click(option('Synthetic'))
  await waitFor(() => expect(screen.queryByRole('combobox')).not.toBeInTheDocument())
  const recent = JSON.parse(localStorage.getItem('securo.cmdk.recent')!)[0]
  expect(recent).toMatchObject({ label: 'Saldo inicial', source: 'opening_balance', icon: 'transaction' })
  await user.click(screen.getByRole('button', { name: 'Open palette' }))
  expect(screen.getByText('Openingsbalans')).toBeVisible()
  await act(async () => { await i18n.changeLanguage('en') })
  expect(screen.getByText('Opening balance')).toBeVisible()
  // Re-selecting a recent must not lose its provenance or freeze its translation.
  await user.click(screen.getByText('Opening balance'))
  await user.click(screen.getByRole('button', { name: 'Open palette' }))
  await act(async () => { await i18n.changeLanguage('pt-BR') })
  expect(screen.getByText('Saldo inicial')).toBeVisible()
  // A legacy result with no provenance remains raw when saved as a recent.
  await user.type(screen.getByRole('combobox'), 'saldo')
  await waitFor(() => expect(screen.getByRole('option', { name: /Legacy/ })).toBeVisible())
  await user.click(screen.getByRole('option', { name: /Legacy/ }))
  await user.click(screen.getByRole('button', { name: 'Open palette' }))
  await act(async () => { await i18n.changeLanguage('nl') })
  expect(within(screen.getByRole('option', { name: /Legacy/ })).getByText('Saldo inicial')).toBeVisible()
  expect(screen.getByText('Openingsbalans')).toBeVisible()
  await user.type(screen.getByRole('combobox'), 'saldo')
  await waitFor(() => expect(screen.getByRole('option', { name: /Account/ })).toBeVisible())
  await user.click(screen.getByRole('option', { name: /Account/ }))
  await user.click(screen.getByRole('button', { name: 'Open palette' }))
  expect(within(screen.getByRole('option', { name: /Account/ })).getByText('Saldo inicial')).toBeVisible()
  expect(hits).toEqual(original)
})

// Characterization of the shared display contract across every shipped bundle.
it.each(SUPPORTED_LANGS)('ships dedicated primary copy without changing badge copy ($code)', async ({ code }) => {
  await i18n.changeLanguage(code)
  const title = i18n.getResource(code, 'translation', 'accounts.openingBalanceDescription')
  const badge = i18n.getResource(code, 'translation', 'accounts.openingBalance')
  expect(typeof title).toBe('string')
  expect(title).not.toBe('accounts.openingBalanceDescription')
  expect(transactionDescription('raw API text', 'opening_balance', i18n.t)).toBe(title)
  expect(badge).toBeTruthy()
  for (const text of ['Saldo inicial', 'opening balance', 'Opening balance', 'Grocery store', 'lowercase user text']) {
    for (const source of ['manual', 'import', undefined, null, { source: 'opening_balance' }]) {
      expect(transactionDescription(text, source, i18n.t)).toBe(text)
    }
  }
})