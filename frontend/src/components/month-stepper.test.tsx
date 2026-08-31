import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { MonthStepper } from '@/components/month-stepper'
import { renderWithProviders } from '@/test/utils'

describe('MonthStepper', () => {
  it('shows the selected month', () => {
    renderWithProviders(
      <MonthStepper value="2026-06" onChange={vi.fn()} locale="en-US" />,
    )

    expect(screen.getByTitle(/June 2026/)).toBeInTheDocument()
  })

  it('steps back a month', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <MonthStepper
        value="2026-06"
        onChange={onChange}
        locale="en-US"
        prevLabel="Previous month"
        nextLabel="Next month"
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Previous month' }))

    expect(onChange).toHaveBeenCalledWith('2026-05')
  })

  it('steps forward a month', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <MonthStepper
        value="2026-06"
        onChange={onChange}
        locale="en-US"
        prevLabel="Previous month"
        nextLabel="Next month"
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Next month' }))

    expect(onChange).toHaveBeenCalledWith('2026-07')
  })

  it('crosses the year boundary going back from January', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <MonthStepper
        value="2026-01"
        onChange={onChange}
        locale="en-US"
        prevLabel="Previous month"
        nextLabel="Next month"
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Previous month' }))

    expect(onChange).toHaveBeenCalledWith('2025-12')
  })

  it('crosses the year boundary going forward from December', async () => {
    const onChange = vi.fn()
    const { user } = renderWithProviders(
      <MonthStepper
        value="2026-12"
        onChange={onChange}
        locale="en-US"
        prevLabel="Previous month"
        nextLabel="Next month"
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Next month' }))

    expect(onChange).toHaveBeenCalledWith('2027-01')
  })

  it('renders the label in the given locale', () => {
    renderWithProviders(
      <MonthStepper value="2026-05" onChange={vi.fn()} locale="pt-BR" />,
    )

    expect(screen.getByTitle(/maio de 2026/i)).toBeInTheDocument()
  })

  it('holds no state of its own, so the parent stays the source of truth', () => {
    const onChange = vi.fn()
    const { rerender } = renderWithProviders(
      <MonthStepper value="2026-06" onChange={onChange} locale="en-US" />,
    )

    rerender(
      <MonthStepper value="2026-09" onChange={onChange} locale="en-US" />,
    )

    expect(screen.getByTitle(/September 2026/)).toBeInTheDocument()
  })
})
