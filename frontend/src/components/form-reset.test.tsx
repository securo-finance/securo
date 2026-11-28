import { fireEvent, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { TransferDialog } from './transfer-dialog'
import { renderWithProviders } from '@/test/utils'
import type { Account } from '@/types'

vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: null }) }))

it('preserves a transfer draft when accounts reorder and resets it on reopen', () => {
  const accounts = [
    { id: 'a', name: 'Source', type: 'checking', currency: 'USD' },
    { id: 'b', name: 'Destination', type: 'checking', currency: 'EUR' },
  ] as Account[]
  const props = { accounts, onClose: vi.fn(), onSave: vi.fn(), loading: false }
  const { rerender } = renderWithProviders(<TransferDialog {...props} open />)
  fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'b' } })
  const amounts = screen.getAllByRole('spinbutton')
  fireEvent.change(amounts[0], { target: { value: '23' } })
  fireEvent.change(amounts[1], { target: { value: '21' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Description' }), { target: { value: 'Draft transfer' } })
  fireEvent.change(screen.getByRole('textbox', { name: /Notes/ }), { target: { value: 'Draft notes' } })

  const reorderedProps = { ...props, accounts: [...accounts].reverse() }
  rerender(<TransferDialog {...reorderedProps} open />)
  expect(screen.getAllByRole('combobox')[0]).toHaveValue('a')
  expect(screen.getAllByRole('combobox')[1]).toHaveValue('b')
  expect(screen.getAllByRole('spinbutton')[0]).toHaveValue(23)
  expect(screen.getAllByRole('spinbutton')[1]).toHaveValue(21)
  expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('Draft transfer')
  expect(screen.getByRole('textbox', { name: /Notes/ })).toHaveValue('Draft notes')

  rerender(<TransferDialog {...reorderedProps} open={false} />)
  rerender(<TransferDialog {...reorderedProps} open />)
  expect(screen.getAllByRole('combobox')[0]).toHaveValue('b')
  expect(screen.getAllByRole('combobox')[1]).toHaveValue('')
  expect(screen.getAllByRole('spinbutton')).toHaveLength(1)
  expect(screen.getByRole('spinbutton')).toHaveValue(null)
})
