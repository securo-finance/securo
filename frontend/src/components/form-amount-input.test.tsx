import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FormAmountInput } from './amount-input'
import { renderWithProviders } from '@/test/utils'

let locale = 'en-US'
vi.mock('@/hooks/use-display-locale', () => ({ useDisplayLocale: () => locale }))

function Form() {
  return (
    <form aria-label="budget">
      <input name="note" aria-label="note" />
      <FormAmountInput name="amount" aria-label="amount" defaultValue="1234.56" />
    </form>
  )
}

describe('FormAmountInput', () => {
  it('converts its text when the locale resolves, without resetting the form', () => {
    locale = 'en-US'
    const { rerender } = renderWithProviders(<Form />)
    fireEvent.change(screen.getByLabelText('note'), { target: { value: 'groceries' } })

    locale = 'de-DE'
    rerender(<Form />)

    expect(screen.getByLabelText('amount')).toHaveValue('1234,56')
    expect(screen.getByLabelText('note')).toHaveValue('groceries')
    const data = new FormData(screen.getByRole('form', { name: 'budget' }) as HTMLFormElement)
    expect(data.get('amount')).toBe('1234,56')
  })

  it('keeps what the user typed', () => {
    locale = 'de-DE'
    renderWithProviders(<Form />)
    fireEvent.change(screen.getByLabelText('amount'), { target: { value: '99,5' } })
    expect(screen.getByLabelText('amount')).toHaveValue('99,5')
  })
})
