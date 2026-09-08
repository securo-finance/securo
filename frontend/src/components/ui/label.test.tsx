import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { renderWithProviders } from '@/test/utils'

describe('Label', () => {
  it('renders its text', () => {
    renderWithProviders(<Label>Category</Label>)

    expect(screen.getByText('Category')).toBeInTheDocument()
  })

  it('associates with the control it names, so the field is reachable by label', async () => {
    const { user } = renderWithProviders(
      <>
        <Label htmlFor="payee">Payee</Label>
        <Input id="payee" />
      </>,
    )

    const input = screen.getByLabelText('Payee')
    expect(input).toBeInTheDocument()

    // Clicking the label must focus the input, which is the whole point of
    // the association.
    await user.click(screen.getByText('Payee'))
    expect(input).toHaveFocus()
  })
})
