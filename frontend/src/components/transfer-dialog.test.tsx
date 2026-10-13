import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TransferDialog } from './transfer-dialog'
import { renderWithProviders } from '@/test/utils'
import type { Account } from '@/types'

vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: null }) }))
// space_comma ("1 234,56"), the format issue #1072 was reported on.
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'fr-FR',
  useDateLocale: () => 'fr-FR',
}))

const accounts = [
  { id: 'a', name: 'Source', type: 'checking', currency: 'EUR' },
  { id: 'b', name: 'Destination', type: 'checking', currency: 'EUR' },
] as Account[]

function setup() {
  const onSave = vi.fn()
  renderWithProviders(
    <TransferDialog
      open
      accounts={accounts}
      onClose={vi.fn()}
      onSave={onSave}
      loading={false}
      defaultFromAccountId="a"
    />,
  )
  fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'b' } })
  const amount = document.querySelector<HTMLInputElement>('input[inputmode="decimal"]')!
  return { onSave, amount }
}

describe('TransferDialog amount', () => {
  it.each([
    ['1 234,56', 1234.56],
    ['50.25', 50.25],
  ])('saves %s typed under the display locale as %d', (typed, expected) => {
    const { onSave, amount } = setup()
    fireEvent.change(amount, { target: { value: typed } })
    fireEvent.submit(amount.closest('form')!)
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ amount: expected }))
  })

  it('refuses an unparseable amount instead of saving NaN', () => {
    const { onSave, amount } = setup()
    fireEvent.change(amount, { target: { value: '12abc' } })
    expect(amount).toHaveAttribute('aria-invalid', 'true')
    fireEvent.submit(amount.closest('form')!)
    expect(onSave).not.toHaveBeenCalled()
  })
})
