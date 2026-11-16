import { fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LoanScheduleCard } from '@/components/loan-schedule-card'
import { formatCurrency } from '@/lib/format'
import { renderWithProviders, t } from '@/test/utils'
import type { LoanSchedule } from '@/types'

const loanSchedule = vi.fn()

vi.mock('@/lib/api', () => ({
  accounts: { loanSchedule: (id: string) => loanSchedule(id) },
  categories: { list: async () => [] },
  categoryGroups: { list: async () => [] },
  timezones: { list: async () => ({ default: 'UTC', available: [] }) },
}))

// The date picker reads the display locale from the signed-in user.
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

const installments = [
  { number: 1, due_date: '2026-01-10', payment: 888.49, interest: 100, principal: 788.49, balance: 9211.51 },
  { number: 2, due_date: '2026-02-10', payment: 888.49, interest: 92.12, principal: 796.37, balance: 8415.14 },
  { number: 3, due_date: '2026-03-10', payment: 888.47, interest: 84.15, principal: 804.32, balance: 0 },
]

function schedule(overrides: Partial<LoanSchedule> = {}): LoanSchedule {
  return {
    account_id: 'loan',
    currency: 'EUR',
    current_debt: 8000,
    configured: true,
    missing: [],
    monthly_payment: 888.49,
    payments_due: 1,
    remaining_payments: 2,
    next_payment: installments[1],
    scheduled_balance: 9211.51,
    ahead_by: 1211.51,
    total_interest: 276.27,
    total_paid: 2665.45,
    payoff_date: '2026-03-10',
    projected_payoff_date: '2026-03-10',
    paid_off: false,
    installments,
    ...overrides,
  }
}

const props = { accountId: 'loan', locale: 'en-US', dateLocale: 'en-US', mask: (value: string) => value }
const money = (value: number) => formatCurrency(value, 'EUR', 'en-US')

describe('LoanScheduleCard', () => {
  beforeEach(() => loanSchedule.mockReset())

  // The layout test below stands in for jsdom's missing geometry on the
  // prototype, so put both back or it leaks into every later test.
  const clientHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight')
  afterEach(() => {
    if (clientHeight) Object.defineProperty(HTMLElement.prototype, 'clientHeight', clientHeight)
    vi.restoreAllMocks()
  })

  it('shows the next payment split and how far ahead the loan is', async () => {
    loanSchedule.mockResolvedValue(schedule())

    renderWithProviders(<LoanScheduleCard {...props} />)

    expect(await screen.findByText(money(888.49))).toBeInTheDocument()
    expect(screen.getByText(t('loans.ahead', { amount: money(1211.51) }))).toBeInTheDocument()
    expect(screen.getByText(t('loans.remainingValue', { remaining: 2, total: 3 }))).toBeInTheDocument()
    expect(
      screen.getByText(
        t('loans.nextPaymentSplit', {
          date: new Date('2026-02-10T00:00:00').toLocaleDateString('en-US'),
          interest: money(92.12),
          principal: money(796.37),
        }),
      ),
    ).toBeInTheDocument()
  })

  it('says when the loan is behind the contract', async () => {
    loanSchedule.mockResolvedValue(schedule({ ahead_by: -300 }))

    renderWithProviders(<LoanScheduleCard {...props} />)

    expect(await screen.findByText(t('loans.behind', { amount: money(300) }))).toBeInTheDocument()
  })

  it('marks the current installment so the table can scroll to it', async () => {
    loanSchedule.mockResolvedValue(schedule())
    renderWithProviders(<LoanScheduleCard {...props} />)

    fireEvent.click(await screen.findByText(t('loans.showSchedule')))

    const current = await screen.findByRole('row', { current: 'step' })
    // next_payment is installment 2, so the first row must not be the marked one.
    expect(current).toHaveTextContent('2')
    expect(screen.getAllByRole('row', { current: 'step' })).toHaveLength(1)
  })

  it('scrolls the table so the current installment is in view', async () => {
    // jsdom has no layout, so stand in for it: the table shows 384px at a time
    // and the current row sits 792px down, the way installment 24 of a
    // mortgage does.
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, value: 384 })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return { top: this.tagName === 'TR' ? 792 : 0 } as DOMRect
    })
    loanSchedule.mockResolvedValue(schedule())
    renderWithProviders(<LoanScheduleCard {...props} />)

    fireEvent.click(await screen.findByText(t('loans.showSchedule')))
    const container = (await screen.findByRole('row', { current: 'step' })).closest('div')!

    // Centred: 792 - 384 / 2. Without this the table opens on installment 1.
    expect(container.scrollTop).toBe(600)
  })

  it('opens the full schedule on demand', async () => {
    loanSchedule.mockResolvedValue(schedule())

    renderWithProviders(<LoanScheduleCard {...props} />)
    fireEvent.click(await screen.findByRole('button', { name: t('loans.showSchedule') }))

    expect(screen.getAllByRole('row')).toHaveLength(installments.length + 1)
    expect(screen.getByText(money(888.47))).toBeInTheDocument()
  })

  it('asks for the contract terms when they are missing', async () => {
    loanSchedule.mockResolvedValue(
      schedule({ configured: false, missing: ['loan_interest_rate'], next_payment: null, installments: [] }),
    )

    renderWithProviders(<LoanScheduleCard {...props} />)

    expect(await screen.findByText(t('loans.notConfigured'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('loans.showSchedule') })).not.toBeInTheDocument()
  })

  it('offers to update the balance only to members who can write', async () => {
    loanSchedule.mockResolvedValue(schedule())

    const { unmount } = renderWithProviders(<LoanScheduleCard {...props} />)
    await screen.findByText(money(888.49))
    expect(screen.queryByRole('button', { name: t('loans.adjustAction') })).not.toBeInTheDocument()
    unmount()

    renderWithProviders(<LoanScheduleCard {...props} canWrite />)
    fireEvent.click(await screen.findByRole('button', { name: t('loans.adjustAction') }))

    expect(await screen.findByRole('dialog', { name: t('loans.adjustTitle') })).toBeInTheDocument()
  })

  it('lets a loan without terms be updated from the statement', async () => {
    loanSchedule.mockResolvedValue(
      schedule({ configured: false, missing: ['loan_interest_rate'], next_payment: null, installments: [] }),
    )

    renderWithProviders(<LoanScheduleCard {...props} canWrite />)

    expect(await screen.findByRole('button', { name: t('loans.adjustAction') })).toBeInTheDocument()
  })
})
