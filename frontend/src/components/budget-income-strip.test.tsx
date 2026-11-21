import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import { BudgetIncomeStrip } from '@/components/budget-income-strip'
import i18n, { i18nReady } from '@/lib/i18n'
import { renderWithProviders, t } from '@/test/utils'
import type { Budget, ExpectedIncome } from '@/types'

const api = vi.hoisted(() => ({
  canWrite: true,
  privacyMode: false,
  mask: vi.fn((value: string) => value),
  budgets: { expectedIncome: { get: vi.fn(), put: vi.fn() } },
}))

vi.mock('@/lib/api', () => ({ budgets: api.budgets }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: api.mask, privacyMode: api.privacyMode, MASK: '••••' }),
}))

function strip(savedBudgets: Budget[]) {
  return renderWithProviders(
    <BudgetIncomeStrip
        month="2026-10-01"
        savedBudgets={savedBudgets}
        canWrite={api.canWrite}
        currency="USD"
        locale="en-US"
        fmt={(amount) => api.mask(`$${amount.toFixed(2)}`)}
    />,
  )
}

const rent: Budget = { id: 'b-rent', user_id: 'u', category_id: 'rent', amount: 900, month: '2026-10-01', is_recurring: true }
const hidden: Budget = { id: 'b-hidden', user_id: 'u', category_id: 'hidden', amount: 120, month: '2026-10-01', is_recurring: true }
const income: ExpectedIncome = { month: '2026-10-01', amount: 5000, currency: 'USD', amount_primary: 5000 }

beforeEach(() => {
  vi.clearAllMocks()
  api.canWrite = true
  api.privacyMode = false
  api.mask.mockImplementation((value: string) => value)
  api.budgets.expectedIncome.get.mockResolvedValue(null)
  api.budgets.expectedIncome.put.mockResolvedValue(income)
})

describe('BudgetIncomeStrip', () => {
  it('shows the saved row after a successful save and the setter after clearing', async () => {
    let stored: ExpectedIncome | null = null
    api.budgets.expectedIncome.get.mockImplementation(async () => stored)
    api.budgets.expectedIncome.put.mockImplementation(async ({ amount }: { amount: string | null }) => {
      stored = amount === null ? null : { ...income, amount: Number(amount), amount_primary: Number(amount) }
      return stored
    })
    const { user } = await strip([rent])

    const setButton = await screen.findByRole('button', { name: t('budgets.incomeSet') })
    expect(setButton).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: t('budgets.incomeLabel') }), '5000')
    await user.click(setButton)

    // The invalidation refetches the write: the row replaces the setter.
    expect(
      await screen.findByText(t('budgets.incomeLeftShare', { percent: 82, amount: '$4100.00' })),
    ).toBeInTheDocument()
    expect(api.budgets.expectedIncome.put).toHaveBeenCalledWith({ month: '2026-10-01', amount: '5000' })

    await user.click(screen.getByRole('button', { name: t('budgets.incomeClear') }))

    // Clearing refetches the empty row and the setter comes back.
    expect(await screen.findByRole('button', { name: t('budgets.incomeSet') })).toBeInTheDocument()
    expect(api.budgets.expectedIncome.put).toHaveBeenLastCalledWith({ month: '2026-10-01', amount: null })
  })

  it('shows the share of the income left and the amount', async () => {
    api.budgets.expectedIncome.get.mockResolvedValue(income)
    await strip([rent, hidden])

    expect(await screen.findByText(t('budgets.incomePlanned', { amount: '$1020.00' }))).toBeInTheDocument()
    const share = screen.getByText(t('budgets.incomeLeftShare', { percent: 79, amount: '$3980.00' }))
    expect(share).toHaveClass('text-emerald-700')
  })

  it('treats a zero difference in tenths as neutral, not short', async () => {
    api.budgets.expectedIncome.get.mockResolvedValue({ ...income, amount: 0.3, amount_primary: 0.3 })
    await strip([
      { ...rent, id: 'b-a', amount: 0.1 },
      { ...rent, id: 'b-b', amount: 0.2 },
    ])

    const zero = await screen.findByText(t('budgets.incomeLeftShare', { percent: 0, amount: '$0.00' }))
    expect(zero).toHaveClass('text-muted-foreground')
  })

  it('floors the share with integer arithmetic', async () => {
    api.budgets.expectedIncome.get.mockResolvedValue({ ...income, amount: 100, amount_primary: 100 })
    await strip([{ ...rent, id: 'b-rent-71', amount: 71 }])

    expect(
      await screen.findByText(t('budgets.incomeLeftShare', { percent: 29, amount: '$29.00' })),
    ).toBeInTheDocument()
  })

  it('shows the share the income is short by, and never divides by a zero planned total', async () => {
    api.budgets.expectedIncome.get.mockResolvedValue({ ...income, amount: 500, amount_primary: 500 })
    const withLimits = await strip([rent, hidden])

    // 520 short of a 500 income is 104% of it.
    const short = await screen.findByText(t('budgets.incomeShortShare', { percent: 104, amount: '$520.00' }))
    expect(short).toHaveClass('text-rose-700')

    // A month without saved limits leaves the whole income: 100%.
    withLimits.unmount()
    await strip([])
    expect(await screen.findByText(t('budgets.incomeLeftShare', { percent: 100, amount: '$500.00' }))).toBeInTheDocument()
  })

  it('leaves the percent out when there is no income to take a share of', async () => {
    api.budgets.expectedIncome.get.mockResolvedValue({ ...income, amount: 0, amount_primary: 0 })
    await strip([rent])

    const short = await screen.findByText(t('budgets.incomeShort', { amount: '$900.00' }))
    expect(short).toHaveClass('text-rose-700')
  })

  it('masks income, planned and difference in privacy mode', async () => {
    api.privacyMode = true
    api.mask.mockImplementation((value: string) => `M${value}`)
    api.budgets.expectedIncome.get.mockResolvedValue(income)
    await strip([rent])

    expect(await screen.findByText('M$5000.00')).toBeInTheDocument()
    expect(screen.getByText(t('budgets.incomePlanned', { amount: 'M$900.00' }))).toBeInTheDocument()
    expect(screen.getByText(t('budgets.incomeLeftShare', { percent: 82, amount: 'M$4100.00' }))).toBeInTheDocument()
  })

  it('hides the setter and the clear button from a viewer', async () => {
    api.canWrite = false
    api.budgets.expectedIncome.get.mockResolvedValue(income)
    await strip([rent])

    expect(await screen.findByText(t('budgets.incomePlanned', { amount: '$900.00' }))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('budgets.incomeClear') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('budgets.incomeSet') })).not.toBeInTheDocument()
  })

  it('compares the raw amount when it is already in the display currency', async () => {
    api.budgets.expectedIncome.get.mockResolvedValue({ ...income, amount: 1400, amount_primary: null })
    await strip([rent])

    expect(await screen.findByText('$1400.00')).toBeInTheDocument()
    expect(screen.getByText(t('budgets.incomeLeftShare', { percent: 35, amount: '$500.00' }))).toBeInTheDocument()
  })

  it('shows an unconverted income in its own currency and withholds the comparison', async () => {
    api.budgets.expectedIncome.get.mockResolvedValue({
      ...income,
      amount: 1400,
      currency: 'EUR',
      amount_primary: null,
    })
    await strip([rent])

    expect(await screen.findByText('€1,400.00')).toBeInTheDocument()
    expect(
      screen.queryByText(t('budgets.incomeLeftShare', { percent: 35, amount: '$500.00' })),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/of income left|of income short/)).not.toBeInTheDocument()
  })

  it('reads the month in the strip and the page query once', async () => {
    await strip([rent])
    await waitFor(() => expect(api.budgets.expectedIncome.get).toHaveBeenCalledTimes(1))
  })

  it('marks an invalid draft and points its message at the field', async () => {
    const { user } = await strip([rent])
    const input = await screen.findByRole('textbox', { name: t('budgets.incomeLabel') })
    await user.type(input, 'abc')

    expect(input).toHaveAttribute('aria-invalid', 'true')
    const describedBy = input.getAttribute('aria-describedby')
    expect(describedBy).toBeTruthy()
    expect(document.getElementById(describedBy as string)).toHaveTextContent(t('budgets.invalidAmount'))
  })

  it('reports a failed read', async () => {
    api.budgets.expectedIncome.get.mockRejectedValue(new Error('boom'))
    await strip([rent])
    expect(await screen.findByRole('alert')).toHaveTextContent(t('budgets.incomeLoadError'))
  })

  it('keeps the layout logical under RTL, in Hebrew', async () => {
    await i18nReady
    await i18n.changeLanguage('he')
    try {
      expect(document.documentElement.dir).toBe('rtl')
      api.budgets.expectedIncome.get.mockResolvedValue(income)
      await strip([rent, hidden])
      await screen.findByText(t('budgets.incomePlanned', { amount: '$1020.00' }))
      const container = screen.getByText(t('budgets.incomeLeftShare', { percent: 79, amount: '$3980.00' })).closest('div')
      expect(container?.className ?? '').not.toMatch(/(^| )((ml|mr|pl|pr|left|right)-|text-left|text-right)/)
    } finally {
      await i18n.changeLanguage('en')
    }
  })
})

afterAll(async () => {
  await i18n.changeLanguage('en')
})
