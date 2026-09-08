import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { Badge } from '@/components/ui/badge'
import { renderWithProviders } from '@/test/utils'

describe('Badge', () => {
  it('renders its content', () => {
    renderWithProviders(<Badge>Pending</Badge>)

    expect(screen.getByText('Pending')).toBeInTheDocument()
  })

  it('defaults to the default variant', () => {
    renderWithProviders(<Badge>Cleared</Badge>)

    expect(screen.getByText('Cleared')).toHaveAttribute('data-variant', 'default')
  })

  it('records the requested variant', () => {
    renderWithProviders(<Badge variant="destructive">Overdue</Badge>)

    expect(screen.getByText('Overdue')).toHaveAttribute(
      'data-variant',
      'destructive',
    )
  })

  it('renders as a link when asChild is set', () => {
    renderWithProviders(
      <Badge asChild>
        <a href="/rules">3 rules</a>
      </Badge>,
    )

    expect(screen.getByRole('link', { name: '3 rules' })).toHaveAttribute(
      'href',
      '/rules',
    )
  })

  it('keeps whitespace-nowrap so a long label cannot wrap mid-badge', () => {
    // #693 was a label wrapping inside its container. The class is the
    // contract that prevents it.
    renderWithProviders(<Badge>Starts with</Badge>)

    expect(screen.getByText('Starts with')).toHaveClass('whitespace-nowrap')
  })
})
