import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { Button } from '@/components/ui/button'
import { renderWithProviders } from '@/test/utils'

describe('Button', () => {
  it('renders its label as a real button element', () => {
    renderWithProviders(<Button>Save changes</Button>)

    const button = screen.getByRole('button', { name: 'Save changes' })
    expect(button).toBeInTheDocument()
    expect(button.tagName).toBe('BUTTON')
  })

  it('calls onClick when pressed', async () => {
    const onClick = vi.fn()
    const { user } = renderWithProviders(<Button onClick={onClick}>Confirm</Button>)

    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('does not fire onClick while disabled', async () => {
    const onClick = vi.fn()
    const { user } = renderWithProviders(
      <Button disabled onClick={onClick}>
        Delete
      </Button>,
    )

    const button = screen.getByRole('button', { name: 'Delete' })
    expect(button).toBeDisabled()

    await user.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('exposes variant and size so styling regressions stay assertable', () => {
    renderWithProviders(
      <Button variant="destructive" size="sm">
        Remove
      </Button>,
    )

    const button = screen.getByRole('button', { name: 'Remove' })
    expect(button).toHaveAttribute('data-variant', 'destructive')
    expect(button).toHaveAttribute('data-size', 'sm')
  })

  it('renders as the child element when asChild is set', () => {
    renderWithProviders(
      <Button asChild>
        <a href="/accounts">Go to accounts</a>
      </Button>,
    )

    const link = screen.getByRole('link', { name: 'Go to accounts' })
    expect(link).toHaveAttribute('href', '/accounts')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('keeps a caller className alongside the variant classes', () => {
    renderWithProviders(<Button className="w-full">Wide</Button>)

    expect(screen.getByRole('button', { name: 'Wide' })).toHaveClass('w-full')
  })
})
