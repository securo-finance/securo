import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { PageHeader } from '@/components/page-header'
import { renderWithProviders } from '@/test/utils'

describe('PageHeader', () => {
  it('renders the title as the page heading', () => {
    // One h1 per screen is what lets a screen-reader user jump to the
    // content; the section line above it is not a heading.
    renderWithProviders(<PageHeader section="Money" title="Transactions" />)

    expect(screen.getByRole('heading', { level: 1, name: 'Transactions' })).toBeInTheDocument()
    expect(screen.getAllByRole('heading')).toHaveLength(1)
  })
})
