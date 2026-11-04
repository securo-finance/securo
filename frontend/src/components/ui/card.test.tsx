import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { renderWithProviders } from '@/test/utils'

describe('Card', () => {
  it('renders every slot it is given', () => {
    renderWithProviders(
      <Card>
        <CardHeader>
          <CardTitle>Net worth</CardTitle>
          <CardDescription>Across all accounts</CardDescription>
          <CardAction>
            <button type="button">Refresh</button>
          </CardAction>
        </CardHeader>
        <CardContent>R$ 12.480,00</CardContent>
        <CardFooter>Updated a minute ago</CardFooter>
      </Card>,
    )

    expect(screen.getByText('Net worth')).toBeInTheDocument()
    expect(screen.getByText('Across all accounts')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument()
    expect(screen.getByText('R$ 12.480,00')).toBeInTheDocument()
    expect(screen.getByText('Updated a minute ago')).toBeInTheDocument()
  })

  it('tags each slot so layout rules stay addressable', () => {
    const { container } = renderWithProviders(
      <Card>
        <CardHeader>
          <CardTitle>Title</CardTitle>
        </CardHeader>
        <CardContent>Body</CardContent>
      </Card>,
    )

    expect(container.querySelector('[data-slot="card"]')).toBeInTheDocument()
    expect(
      container.querySelector('[data-slot="card-header"]'),
    ).toBeInTheDocument()
    expect(
      container.querySelector('[data-slot="card-title"]'),
    ).toBeInTheDocument()
    expect(
      container.querySelector('[data-slot="card-content"]'),
    ).toBeInTheDocument()
  })

  it('renders a card with no optional slots', () => {
    renderWithProviders(<Card>Bare</Card>)

    expect(screen.getByText('Bare')).toBeInTheDocument()
  })
})
