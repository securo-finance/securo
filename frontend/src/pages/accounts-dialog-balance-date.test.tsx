import { fireEvent, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import type { Account } from '@/types'

vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  currencies: { list: vi.fn().mockResolvedValue([]) },
}))
// A plain input stands in for the calendar popover.
vi.mock('@/components/ui/date-picker-input', () => ({
  DatePickerInput: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <input aria-label="balance date" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}))

import { AccountDialog } from './accounts'

const manual = {
  id: 'acc-1',
  connection_id: null,
  name: 'Checking',
  display_name: null,
  type: 'checking',
  balance: 1000,
  currency: 'USD',
  balance_date: '2026-08-01',
  credit_limit: null,
  statement_close_day: null,
  payment_due_day: null,
} as Account

function renderDialog(account: Account | null) {
  const onSave = vi.fn()
  renderWithProviders(
    <AccountDialog open onClose={vi.fn()} account={account} onSave={onSave} loading={false} />,
  )
  return onSave
}

it('opens with the opening balance date and keeps it on an unrelated edit (#1204)', () => {
  const onSave = renderDialog(manual)

  expect(screen.getByLabelText('balance date')).toHaveValue('2026-08-01')
  fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Main checking' } })
  fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

  expect(onSave).toHaveBeenCalledTimes(1)
  expect(onSave.mock.calls[0][0]).toMatchObject({ name: 'Main checking' })
  expect(onSave.mock.calls[0][0]).not.toHaveProperty('balance_date')
})

it('sends the date when the user changes it', () => {
  const onSave = renderDialog(manual)

  fireEvent.change(screen.getByLabelText('balance date'), { target: { value: '2026-07-15' } })
  fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

  expect(onSave.mock.calls[0][0]).toMatchObject({ balance_date: '2026-07-15' })
})

it('always sends the date for a new account', () => {
  const onSave = renderDialog(null)

  fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Savings' } })
  fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

  expect(onSave.mock.calls[0][0]).toHaveProperty('balance_date')
})
