import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'

import { IconPicker } from '@/components/icon-picker'
import { renderWithProviders } from '@/test/utils'

function renderPicker(value = 'house') {
  const onChange = vi.fn()
  const view = renderWithProviders(
    <IconPicker value={value} color="#22c55e" onChange={onChange} />,
  )
  const search = screen.getByPlaceholderText(/search icon/i)
  return { ...view, onChange, search }
}

describe('IconPicker', () => {
  it('finds an icon by its English name', () => {
    const { search } = renderPicker()

    fireEvent.change(search, { target: { value: 'cart' } })

    expect(screen.getByTitle('Shopping Cart')).toBeInTheDocument()
    expect(screen.queryByTitle('House')).not.toBeInTheDocument()
  })

  it('finds an icon by the label shown on hover', () => {
    const { search } = renderPicker()

    fireEvent.change(search, { target: { value: 'Shopping Cart' } })

    expect(screen.getByTitle('Shopping Cart')).toBeInTheDocument()
  })

  it('finds an icon by a Portuguese keyword', () => {
    // The catalogue used to carry Portuguese labels, so a pt-BR user who
    // searched "cachorro" found the dog. That has to keep working.
    const { search } = renderPicker()

    fireEvent.change(search, { target: { value: 'cachorro' } })

    expect(screen.getByTitle('Dog')).toBeInTheDocument()
  })

  it('matches a Portuguese keyword typed with accents', () => {
    const { search } = renderPicker()

    fireEvent.change(search, { target: { value: 'Música' } })

    expect(screen.getByTitle('Music')).toBeInTheDocument()
  })

  it('ignores case and surrounding whitespace in the query', () => {
    const { search } = renderPicker()

    fireEvent.change(search, { target: { value: '  DOG  ' } })

    expect(screen.getByTitle('Dog')).toBeInTheDocument()
  })

  it('reports when nothing matches instead of showing an empty grid', () => {
    const { search } = renderPicker()

    fireEvent.change(search, { target: { value: 'zzzznotanicon' } })

    expect(screen.getByText(/no icons found/i)).toBeInTheDocument()
  })

  it('hands the picked icon name back to the caller', () => {
    const { search, onChange } = renderPicker()

    fireEvent.change(search, { target: { value: 'cachorro' } })
    fireEvent.click(screen.getByTitle('Dog'))

    expect(onChange).toHaveBeenCalledWith('dog')
  })

  it('marks the current value as selected', () => {
    const { search } = renderPicker('dog')

    fireEvent.change(search, { target: { value: 'cachorro' } })

    expect(screen.getByTitle('Dog')).toHaveClass('ring-primary')
  })
})
