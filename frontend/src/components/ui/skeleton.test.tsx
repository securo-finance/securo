import { describe, expect, it } from 'vitest'

import { Skeleton } from '@/components/ui/skeleton'
import { renderWithProviders } from '@/test/utils'

describe('Skeleton', () => {
  it('renders a pulsing placeholder', () => {
    const { container } = renderWithProviders(<Skeleton />)

    const skeleton = container.querySelector('[data-slot="skeleton"]')!
    expect(skeleton).toBeInTheDocument()
    expect(skeleton).toHaveClass('animate-pulse')
  })

  it('keeps the caller sizing classes', () => {
    const { container } = renderWithProviders(<Skeleton className="h-8 w-32" />)

    expect(container.querySelector('[data-slot="skeleton"]')).toHaveClass(
      'h-8',
      'w-32',
    )
  })

  it('carries no text, so a loading card announces nothing misleading', () => {
    const { container } = renderWithProviders(<Skeleton className="h-4" />)

    expect(container.textContent).toBe('')
  })
})
