import { fireEvent, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { TransferDialog } from './transfer-dialog'
import { renderWithProviders } from '@/test/utils'
import type { Account } from '@/types'

vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: null }) }))

// Amount fields are decimal text inputs (issue #1072), not number inputs.
const amountInputs = () =>
  Array.from(document.querySelectorAll<HTMLInputElement>('input[inputmode="decimal"]'))

it('preserves a transfer draft on rerender and resets all amounts and accounts on reopen', () => {
  const accounts = [
    { id: 'a', name: 'Source', type: 'checking', currency: 'USD' },
    { id: 'b', name: 'Destination', type: 'checking', currency: 'EUR' },
  ] as Account[]
  const props = { accounts, onClose: vi.fn(), onSave: vi.fn(), loading: false, defaultFromAccountId: 'a' }
  const { rerender } = renderWithProviders(<TransferDialog {...props} open />)
  fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'b' } })
  const amounts = amountInputs()
  fireEvent.change(amounts[0], { target: { value: '23' } })
  fireEvent.change(amounts[1], { target: { value: '21' } })
  rerender(<TransferDialog {...props} open />)
  expect(amountInputs()[0]).toHaveValue('23')
  expect(amountInputs()[1]).toHaveValue('21')
  rerender(<TransferDialog {...props} open={false} />)
  rerender(<TransferDialog {...props} open />)
  expect(screen.getAllByRole('combobox')[0]).toHaveValue('a')
  expect(screen.getAllByRole('combobox')[1]).toHaveValue('')
  expect(amountInputs()).toHaveLength(1)
  expect(amountInputs()[0]).toHaveValue('')
})
