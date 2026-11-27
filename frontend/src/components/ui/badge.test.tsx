import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { Badge } from '@/components/ui/badge'
import { renderWithProviders } from '@/test/utils'

describe('Badge', () => {
  it('keeps whitespace-nowrap so a long label cannot wrap mid-badge', () => {
    // #693 was a label wrapping inside its container. The class is the
    // contract that prevents it.
    renderWithProviders(<Badge>Starts with</Badge>)

    expect(screen.getByText('Starts with')).toHaveClass('whitespace-nowrap')
  })
})
