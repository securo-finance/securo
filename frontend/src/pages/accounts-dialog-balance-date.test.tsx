import { fireEvent, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import { localDateString } from '@/lib/date-utils'
import type { Account } from '@/types'

const timezone = vi.hoisted(() => ({ value: 'UTC' as string | undefined }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => timezone.value }))
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
  // The API serializes the Decimal balance as a string.
  balance: '1000.00' as unknown as number,
  currency: 'USD',
  balance_date: '2026-08-01',
  credit_limit: null,
  statement_close_day: null,
  payment_due_day: null,
} as Account

function renderDialog(account: Account | null) {
  const onSave = vi.fn()
  renderWithProviders(<AccountDialog open onClose={vi.fn()} account={account} onSave={onSave} loading={false} />)
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

it('uses the workspace date when an existing manual balance changes', () => {
  vi.setSystemTime(new Date('2026-11-20T12:00:00Z'))
  try {
    const localDate = localDateString()
    timezone.value = localDate === '2026-11-21' ? 'Pacific/Honolulu' : 'Pacific/Kiritimati'
    const workspaceDate = timezone.value === 'Pacific/Honolulu' ? '2026-11-20' : '2026-11-21'
    expect(workspaceDate).not.toBe(localDate)
    const onSave = renderDialog(manual)

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1200' } })

    expect(screen.getByLabelText('balance date')).toHaveValue(workspaceDate)
    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))
    expect(onSave.mock.calls[0][0]).toMatchObject({ balance: 1200, balance_date: workspaceDate })
  } finally {
    vi.useRealTimers()
    timezone.value = 'UTC'
  }
})

it('allows a different date to be selected after changing the balance', () => {
  const onSave = renderDialog(manual)

  fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1200' } })
  fireEvent.change(screen.getByLabelText('balance date'), { target: { value: '2026-07-15' } })
  fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

  expect(onSave.mock.calls[0][0]).toMatchObject({ balance: 1200, balance_date: '2026-07-15' })
})

it('restores the saved date when the balance returns to its saved value', () => {
  const onSave = renderDialog(manual)

  fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1200' } })
  fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1000' } })

  expect(screen.getByLabelText('balance date')).toHaveValue('2026-08-01')
  fireEvent.click(screen.getByRole('button', { name: t('common.save') }))
  expect(onSave.mock.calls[0][0]).not.toHaveProperty('balance_date')
})

it('waits for the workspace timezone before dating a changed balance', () => {
  timezone.value = undefined
  try {
    const onSave = renderDialog(manual)

    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1200' } })

    expect(screen.getByLabelText('balance date')).toHaveValue('2026-08-01')
    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))
    expect(onSave.mock.calls[0][0]).not.toHaveProperty('balance_date')
  } finally {
    timezone.value = 'UTC'
  }
})

it('keeps a selected date when entering a new account balance', () => {
  renderDialog(null)

  fireEvent.change(screen.getByLabelText('balance date'), { target: { value: '2026-07-15' } })
  fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '1200' } })

  expect(screen.getByLabelText('balance date')).toHaveValue('2026-07-15')
})

it('always sends the date for a new account', () => {
  const onSave = renderDialog(null)

  fireEvent.change(screen.getAllByRole('textbox')[0], { target: { value: 'Savings' } })
  fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

  expect(onSave.mock.calls[0][0]).toHaveProperty('balance_date')
})
