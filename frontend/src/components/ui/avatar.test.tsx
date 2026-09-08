import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import {
  Avatar,
  AvatarFallback,
  AvatarGroup,
  AvatarGroupCount,
} from '@/components/ui/avatar'
import { renderWithProviders } from '@/test/utils'

describe('Avatar', () => {
  it('renders the fallback when no image resolves', () => {
    // jsdom never loads images, which is also what a member with no avatar
    // looks like in the app.
    renderWithProviders(
      <Avatar>
        <AvatarFallback>TN</AvatarFallback>
      </Avatar>,
    )

    expect(screen.getByText('TN')).toBeInTheDocument()
  })

  it('defaults to the default size', () => {
    const { container } = renderWithProviders(
      <Avatar>
        <AvatarFallback>TN</AvatarFallback>
      </Avatar>,
    )

    expect(container.querySelector('[data-slot="avatar"]')).toHaveAttribute(
      'data-size',
      'default',
    )
  })

  it('records the requested size, which the child styling keys off', () => {
    const { container } = renderWithProviders(
      <Avatar size="lg">
        <AvatarFallback>TN</AvatarFallback>
      </Avatar>,
    )

    expect(container.querySelector('[data-slot="avatar"]')).toHaveAttribute(
      'data-size',
      'lg',
    )
  })

  it('renders a group with an overflow count', () => {
    renderWithProviders(
      <AvatarGroup>
        <Avatar>
          <AvatarFallback>A</AvatarFallback>
        </Avatar>
        <Avatar>
          <AvatarFallback>B</AvatarFallback>
        </Avatar>
        <AvatarGroupCount>+3</AvatarGroupCount>
      </AvatarGroup>,
    )

    expect(screen.getByText('A')).toBeInTheDocument()
    expect(screen.getByText('B')).toBeInTheDocument()
    expect(screen.getByText('+3')).toBeInTheDocument()
  })
})
