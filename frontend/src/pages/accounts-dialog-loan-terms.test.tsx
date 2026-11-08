import { fireEvent, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import type { Account } from '@/types'

vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  currencies: { list: vi.fn().mockResolvedValue([]) },
  assets: { list: vi.fn().mockResolvedValue([]) },
}))
// A plain input stands in for the calendar popover.
vi.mock('@/components/ui/date-picker-input', () => ({
  DatePickerInput: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <input value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}))

import { AccountDialog } from './accounts'

const loan = {
  id: 'loan-1',
  connection_id: null,
  name: 'Car loan',
  display_name: null,
  type: 'loan',
  balance: -30000,
  currency: 'BRL',
  balance_date: '2026-08-01',
  credit_limit: null,
  statement_close_day: null,
  payment_due_day: null,
  secured_asset_id: null,
  loan_kind: 'auto',
  loan_original_amount: 50000,
  loan_interest_rate: 1.2,
  loan_rate_period: 'monthly',
  loan_term_months: 48,
  loan_first_payment_date: '2026-02-10',
  loan_amortization: 'price',
} as Account

function termsSection() {
  return screen.getByText(t('accounts.loanTerms')).closest('details') as HTMLDetailsElement
}

it('keeps the contract terms open while the amount is cleared', () => {
  renderWithProviders(<AccountDialog open onClose={vi.fn()} account={loan} onSave={vi.fn()} loading={false} />)

  expect(termsSection().open).toBe(true)
  const [amount] = within(termsSection()).getAllByRole('spinbutton')
  fireEvent.change(amount, { target: { value: '' } })

  expect(termsSection().open).toBe(true)
})

it('starts with the contract terms closed for a loan without them', () => {
  const plain = { ...loan, loan_kind: null, loan_original_amount: null, loan_interest_rate: null, loan_term_months: null, loan_first_payment_date: null } as Account
  renderWithProviders(<AccountDialog open onClose={vi.fn()} account={plain} onSave={vi.fn()} loading={false} />)

  expect(termsSection().open).toBe(false)
})
