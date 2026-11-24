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
    const onChoose = vi.fn()
    const { user } = renderWithProviders(<FacetSelect facet={TYPE_FACET} value="code" onChoose={onChoose} />)

    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByRole('option', { name: 'Any' }))

    expect(onChoose).toHaveBeenCalledWith('type', null)
  })
})
