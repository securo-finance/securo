import { useState } from 'react'
import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { FacetSelect, type Facet } from './plugins'
import { renderWithProviders } from '@/test/utils'

const TYPE_FACET: Facet = {
  key: 'type',
  title: 'Type',
  options: [
    { value: 'data', label: 'Data', count: 3 },
    { value: 'code', label: 'Code', count: 8 },
  ],
}

describe('FacetSelect', () => {
  it('anchors the open menu to the chip instead of leaving it at the end of the page', async () => {
    // Radix places the menu against the trigger's value node, which only
    // `SelectValue` registers. With a bare span in its place the menu is
    // rendered but never positioned, so it opens below the whole page and no
    // touch ever reaches it. jsdom has no layout, so the check is that the
    // wrapper received the offsets Radix writes once it anchors.
    const { user } = renderWithProviders(<FacetSelect facet={TYPE_FACET} value={null} onChoose={vi.fn()} />)

    await user.click(screen.getByRole('combobox', { name: 'Type' }))

    const listbox = await screen.findByRole('listbox')
    const wrapper = listbox.parentElement as HTMLElement
    expect(wrapper.style.position).toBe('fixed')
    expect(wrapper.style.top || wrapper.style.bottom).not.toBe('')
    expect(wrapper.style.left).not.toBe('')
  })

  it('shows the facet title until one is chosen, then the chosen label', () => {
    const { rerender } = renderWithProviders(<FacetSelect facet={TYPE_FACET} value={null} onChoose={vi.fn()} />)
    expect(screen.getByRole('combobox')).toHaveTextContent('Type')

    rerender(<FacetSelect facet={TYPE_FACET} value="code" onChoose={vi.fn()} />)
    expect(screen.getByRole('combobox')).toHaveTextContent('Code')
  })

  it('clears the filter when Any is chosen', async () => {
    // Controlled by a small harness, so the assertion is on what the user sees:
    // the chip goes back to the facet title, not only that the callback fired.
    const onChoose = vi.fn()
    function Controlled() {
      const [value, setValue] = useState<string | null>('code')
      return (
        <FacetSelect
          facet={TYPE_FACET}
          value={value}
          onChoose={(key, next) => {
            onChoose(key, next)
            setValue(next)
          }}
        />
      )
    }

    const { user } = renderWithProviders(<Controlled />)
    expect(screen.getByRole('combobox')).toHaveTextContent('Code')

    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: 'Any' }))

    expect(onChoose).toHaveBeenCalledWith('type', null)
    expect(screen.getByRole('combobox')).toHaveTextContent('Type')
  })
})
