import { useState } from 'react'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DescriptionInput } from './description-input'
import { renderWithProviders } from '@/test/utils'
import { transactions } from '@/lib/api'
import type { DescriptionSuggestion } from '@/types'

vi.mock('@/lib/api', () => ({
  transactions: { descriptionSuggestions: vi.fn() },
}))

const suggestions: DescriptionSuggestion[] = [
  { description: 'Supermarket Extra', category_id: 'cat-1', payee_id: null },
  { description: 'Big Supermarket', category_id: null, payee_id: 'payee-1' },
]

function Harness({ onPick }: { onPick: (s: DescriptionSuggestion) => void }) {
  const [value, setValue] = useState('')
  return <DescriptionInput value={value} onChange={setValue} onPick={onPick} />
}

function setup() {
  const onPick = vi.fn()
  renderWithProviders(<Harness onPick={onPick} />)
  const input = screen.getByRole('combobox')
  fireEvent.focus(input)
  return { input, onPick }
}

describe('DescriptionInput', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(transactions.descriptionSuggestions).mockResolvedValue(suggestions)
  })

  it('suggests past descriptions and fills the one picked', async () => {
    const { input, onPick } = setup()
    fireEvent.change(input, { target: { value: 'sup' } })

    const option = await screen.findByRole('option', { name: 'Supermarket Extra' })
    expect(transactions.descriptionSuggestions).toHaveBeenCalledWith('sup')
    fireEvent.mouseDown(option)

    expect(input).toHaveValue('Supermarket Extra')
    expect(onPick).toHaveBeenCalledWith(suggestions[0])
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('picks with the keyboard', async () => {
    const { input, onPick } = setup()
    fireEvent.change(input, { target: { value: 'sup' } })
    await screen.findByRole('listbox')

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(input).toHaveValue('Big Supermarket')
    expect(onPick).toHaveBeenCalledWith(suggestions[1])
  })

  it('keeps free text and stays quiet below two characters', async () => {
    const { input } = setup()
    fireEvent.change(input, { target: { value: 's' } })
    await new Promise((r) => setTimeout(r, 300))
    expect(transactions.descriptionSuggestions).not.toHaveBeenCalled()

    fireEvent.change(input, { target: { value: 'something new' } })
    await waitFor(() => expect(transactions.descriptionSuggestions).toHaveBeenCalled())
    expect(input).toHaveValue('something new')
  })

  it('hides results from the previous query while the next one is debounced', async () => {
    const { input } = setup()
    fireEvent.change(input, { target: { value: 'sup' } })
    await screen.findByRole('listbox')

    fireEvent.change(input, { target: { value: 'market' } })
    expect(screen.queryByRole('option', { name: 'Supermarket Extra' })).not.toBeInTheDocument()
  })
})
