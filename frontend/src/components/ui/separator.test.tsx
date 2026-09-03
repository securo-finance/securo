import { describe, expect, it } from 'vitest'

import { Separator } from '@/components/ui/separator'
import { renderWithProviders } from '@/test/utils'

describe('Separator', () => {
  it('defaults to a horizontal decorative rule', () => {
    const { container } = renderWithProviders(<Separator />)

    const separator = container.querySelector('[data-slot="separator"]')!
    expect(separator).toHaveAttribute('data-orientation', 'horizontal')
    // Decorative separators are hidden from the accessibility tree, which is
    // what we want for pure visual dividers.
    expect(separator).toHaveAttribute('role', 'none')
  })

  it('supports a vertical orientation', () => {
    const { container } = renderWithProviders(<Separator orientation="vertical" />)

    expect(container.querySelector('[data-slot="separator"]')).toHaveAttribute(
      'data-orientation',
      'vertical',
    )
  })

  it('becomes a real separator for assistive tech when not decorative', () => {
    const { container } = renderWithProviders(<Separator decorative={false} />)

    expect(container.querySelector('[data-slot="separator"]')).toHaveAttribute(
      'role',
      'separator',
    )
  })
})
