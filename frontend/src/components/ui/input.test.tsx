import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { Input } from '@/components/ui/input'
import { renderWithProviders } from '@/test/utils'

describe('Input', () => {
  it('accepts typed text', async () => {
    const { user } = renderWithProviders(<Input aria-label="Description" />)

    const input = screen.getByLabelText('Description')
    await user.type(input, 'Coffee')

    expect(input).toHaveValue('Coffee')
  })

  it('reports every keystroke to onChange', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <Input aria-label="Amount" onChange={onChange} />,
    )

    await user.type(screen.getByLabelText('Amount'), '250')

    expect(onChange).toHaveBeenCalledTimes(3)
  })

  it('does not accept input while disabled', async () => {
    const { user } = renderWithProviders(<Input aria-label="Locked" disabled />)

    const input = screen.getByLabelText('Locked')
    expect(input).toBeDisabled()

    await user.type(input, 'nope')
    expect(input).toHaveValue('')
  })

  it('passes the type through', () => {
    renderWithProviders(<Input aria-label="Password" type="password" />)

    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
  })

  it('exposes the invalid state that the styling hangs off', () => {
    renderWithProviders(<Input aria-label="Email" aria-invalid />)

    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true')
  })

  it('renders a placeholder', () => {
    renderWithProviders(<Input aria-label="Search" placeholder="Search payees" />)

    expect(screen.getByPlaceholderText('Search payees')).toBeInTheDocument()
  })
})
