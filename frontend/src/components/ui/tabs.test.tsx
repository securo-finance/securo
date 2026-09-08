import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { renderWithProviders } from '@/test/utils'

function Example({
  onValueChange,
}: { onValueChange?: (value: string) => void } = {}) {
  return (
    <Tabs defaultValue="expenses" onValueChange={onValueChange}>
      <TabsList>
        <TabsTrigger value="expenses">Expenses</TabsTrigger>
        <TabsTrigger value="income">Income</TabsTrigger>
        <TabsTrigger value="transfers" disabled>
          Transfers
        </TabsTrigger>
      </TabsList>
      <TabsContent value="expenses">expenses panel</TabsContent>
      <TabsContent value="income">income panel</TabsContent>
      <TabsContent value="transfers">transfers panel</TabsContent>
    </Tabs>
  )
}

describe('Tabs', () => {
  it('shows the default panel and hides the others', () => {
    renderWithProviders(<Example />)

    expect(screen.getByText('expenses panel')).toBeInTheDocument()
    expect(screen.queryByText('income panel')).not.toBeInTheDocument()
  })

  it('marks the active trigger as selected', () => {
    renderWithProviders(<Example />)

    expect(screen.getByRole('tab', { name: 'Expenses' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByRole('tab', { name: 'Income' })).toHaveAttribute(
      'aria-selected',
      'false',
    )
  })

  it('switches panels on click', async () => {
    const { user } = renderWithProviders(<Example />)

    await user.click(screen.getByRole('tab', { name: 'Income' }))

    expect(await screen.findByText('income panel')).toBeInTheDocument()
    expect(screen.queryByText('expenses panel')).not.toBeInTheDocument()
  })

  it('reports the newly selected value', async () => {
    const onValueChange = vi.fn()
    const { user } = renderWithProviders(
      <Example onValueChange={onValueChange} />,
    )

    await user.click(screen.getByRole('tab', { name: 'Income' }))

    expect(onValueChange).toHaveBeenCalledWith('income')
  })

  it('ignores a disabled tab', async () => {
    const onValueChange = vi.fn()
    const { user } = renderWithProviders(
      <Example onValueChange={onValueChange} />,
    )

    await user.click(screen.getByRole('tab', { name: 'Transfers' }))

    expect(onValueChange).not.toHaveBeenCalled()
    expect(screen.getByText('expenses panel')).toBeInTheDocument()
  })

  it('exposes the list variant so line styling stays assertable', () => {
    const { container } = renderWithProviders(
      <Tabs defaultValue="a">
        <TabsList variant="line">
          <TabsTrigger value="a">A</TabsTrigger>
        </TabsList>
        <TabsContent value="a">panel</TabsContent>
      </Tabs>,
    )

    expect(container.querySelector('[data-slot="tabs-list"]')).toHaveAttribute(
      'data-variant',
      'line',
    )
  })
})
