import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { renderWithProviders } from '@/test/utils'

function Example({ onValueChange }: { onValueChange?: (value: string) => void } = {}) {
  return (
    <Select onValueChange={onValueChange}>
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
  it('reports the chosen value', async () => {
    const onValueChange = vi.fn()
    const { user } = renderWithProviders(<Example onValueChange={onValueChange} />)

    await user.click(screen.getByRole('combobox', { name: 'Currency' }))
    await user.click(await screen.findByRole('option', { name: 'Euro' }))

    expect(onValueChange).toHaveBeenCalledWith('EUR')
    // The trigger has to follow. Reporting the value while still showing the
    // placeholder is a real Radix misconfiguration and the mock cannot see it.
    expect(screen.getByRole('combobox', { name: 'Currency' })).toHaveTextContent('Euro')
    expect(screen.queryByText('Pick a currency')).not.toBeInTheDocument()
  })
})
