import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { RecurringMonthSummary } from '@/components/recurring-month-summary'
import { formatCurrency } from '@/lib/format'
import { renderWithProviders } from '@/test/utils'

const api = vi.hoisted(() => ({
  recurring: { monthSummary: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

const summary = (month: string, overrides = {}) => ({
  month: `${month}-01`,
  currency: 'BRL',
  income: '5000.00',
  expenses: '490.00',
  net: '4510.00',
  transfer_count: 0,
  unconverted_count: 0,
  ...overrides,
})

describe('RecurringMonthSummary', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 11, 15))
    api.recurring.monthSummary.mockImplementation(async (month: string) => summary(month))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it("shows this month's income, expenses and net", async () => {
    renderWithProviders(<RecurringMonthSummary />)

    expect(await screen.findByText(formatCurrency(5000, 'BRL', 'en-US'))).toBeInTheDocument()
    expect(screen.getByText(formatCurrency(490, 'BRL', 'en-US'))).toBeInTheDocument()
    expect(screen.getByText(formatCurrency(4510, 'BRL', 'en-US'))).toBeInTheDocument()
    expect(screen.getByText('Income')).toBeInTheDocument()
    expect(screen.getByText('Expenses')).toBeInTheDocument()
    expect(screen.getByText('Net')).toBeInTheDocument()
    expect(api.recurring.monthSummary).toHaveBeenCalledWith('2026-12')
  })

  it('asks for the month the user steps to', async () => {
    const { user } = renderWithProviders(<RecurringMonthSummary />)
    await screen.findByText(formatCurrency(5000, 'BRL', 'en-US'))

    await user.click(screen.getByRole('button', { name: 'Next' }))

    expect(api.recurring.monthSummary).toHaveBeenLastCalledWith('2027-01')
  })

  it('says what was left out of the totals', async () => {
    api.recurring.monthSummary.mockResolvedValue(
      summary('2026-12', { transfer_count: 4, unconverted_count: 1 }),
    )
    renderWithProviders(<RecurringMonthSummary />)

    expect(await screen.findByText('Transfers left out: 4')).toBeInTheDocument()
    expect(screen.getByText('No exchange rate, left out: 1')).toBeInTheDocument()
  })
})
