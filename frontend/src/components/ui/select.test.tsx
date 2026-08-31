import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { renderWithProviders } from '@/test/utils'

function Example({
  onValueChange,
  defaultValue,
  disabled,
}: {
  onValueChange?: (value: string) => void
  defaultValue?: string
  disabled?: boolean
} = {}) {
  return (
    <Select
      defaultValue={defaultValue}
      onValueChange={onValueChange}
      disabled={disabled}
    >
      <SelectTrigger aria-label="Currency">
        <SelectValue placeholder="Pick a currency" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="BRL">Brazilian Real</SelectItem>
        <SelectItem value="USD">US Dollar</SelectItem>
        <SelectItem value="EUR">Euro</SelectItem>
      </SelectContent>
    </Select>
  )
}

describe('Select', () => {
  it('shows the placeholder until something is chosen', () => {
    renderWithProviders(<Example />)

    expect(screen.getByText('Pick a currency')).toBeInTheDocument()
  })

  it('shows the default value instead of the placeholder', () => {
    renderWithProviders(<Example defaultValue="USD" />)

    expect(screen.getByText('US Dollar')).toBeInTheDocument()
    expect(screen.queryByText('Pick a currency')).not.toBeInTheDocument()
  })

  it('keeps its options closed until opened', () => {
    renderWithProviders(<Example />)

    expect(screen.queryByRole('option', { name: 'Euro' })).not.toBeInTheDocument()
  })

  it('opens on click and lists every option', async () => {
    const { user } = renderWithProviders(<Example />)

    await user.click(screen.getByRole('combobox', { name: 'Currency' }))

    expect(
      await screen.findByRole('option', { name: 'Brazilian Real' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'US Dollar' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Euro' })).toBeInTheDocument()
  })

  it('reports the chosen value', async () => {
    const onValueChange = vi.fn()
    const { user } = renderWithProviders(
      <Example onValueChange={onValueChange} />,
    )

    await user.click(screen.getByRole('combobox', { name: 'Currency' }))
    await user.click(await screen.findByRole('option', { name: 'Euro' }))

    expect(onValueChange).toHaveBeenCalledWith('EUR')
    // The trigger has to follow. Reporting the value while still showing the
    // placeholder is a real Radix misconfiguration and the mock cannot see it.
    expect(screen.getByRole('combobox', { name: 'Currency' })).toHaveTextContent(
      'Euro',
    )
    expect(screen.queryByText('Pick a currency')).not.toBeInTheDocument()
  })

  it('does not open while disabled', async () => {
    const { user } = renderWithProviders(<Example disabled />)

    const trigger = screen.getByRole('combobox', { name: 'Currency' })
    expect(trigger).toBeDisabled()

    await user.click(trigger)
    expect(screen.queryByRole('option', { name: 'Euro' })).not.toBeInTheDocument()
  })
})
