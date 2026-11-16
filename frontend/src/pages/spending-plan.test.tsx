import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { renderWithProviders } from '@/test/utils'
import { addDays, daysThrough } from '@/lib/spending-plan'
import SpendingPlanPage from './spending-plan'
import type { SpendingPlanEvent, SpendingPlanTimeline } from '@/types'

const api = vi.hoisted(() => ({
  spendingPlan: {
    get: vi.fn(),
    save: vi.fn(),
    remove: vi.fn(),
    timeline: vi.fn(),
    preview: vi.fn(),
    simulate: vi.fn(),
  },
  accounts: { list: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true, current: { id: 'ws-1' } }),
}))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

const TODAY = '2026-11-12'
const HORIZON = '2026-12-31'
const plan = { daily_allowance: '30.00', balance_floor: '200.00', account_ids: null, horizon_months: 12 }

function event(
  date: string,
  description: string | null,
  amount: number,
  source: SpendingPlanEvent['source'],
  account_name: string | null = null,
): SpendingPlanEvent {
  return { date, description, amount, source, account_name }
}

const EVENTS = [
  event('2026-11-12', null, -10, 'other'),
  event('2026-11-20', 'Salary', 3000, 'recurring', 'Checking'),
  event('2026-11-25', 'Netflix', -50, 'card', 'Visa'),
  event('2026-12-05', 'Rent', -1500, 'recurring', 'Checking'),
  event('2026-12-20', 'Dentist', -200, 'planned', 'Checking'),
  event('2026-12-31', 'Bonus', 5000, 'recurring', 'Checking'),
]

/** A timeline whose points walk the events with 30 a day, 10 of it spent today. */
function timeline(extra: SpendingPlanEvent[] = []): SpendingPlanTimeline {
  const events = [...EVENTS, ...extra].sort((a, b) => a.date.localeCompare(b.date))
  const points = []
  let balance = 1000
  for (let day = TODAY; day <= HORIZON; day = addDays(day, 1)) {
    balance += events.filter((e) => e.date === day).reduce((total, e) => total + e.amount, 0)
    balance -= day === TODAY ? 20 : 30
    points.push({ date: day, balance: Math.round(balance * 100) / 100 })
  }
  return {
    currency: 'USD',
    daily_allowance: 30,
    balance_floor: 200,
    horizon_end: HORIZON,
    // (1000 - 10 + 3000 - 50 - 1500 - 200 - 200 + 10) / 39 days through 20 Dec.
    safe_daily_allowance: 52.56,
    average_daily_surplus: 120,
    covered_until: HORIZON,
    covered_through_horizon: true,
    opening_balance: 1000,
    binding_date: '2026-12-20',
    today: { allowance: 30, spent: 10, remaining: 20 },
    points,
    events,
  }
}

describe('SpendingPlanPage', () => {
  beforeEach(() => {
    window.localStorage.clear()
    Object.values(api.spendingPlan).forEach((fn) => fn.mockReset())
    api.accounts.list.mockResolvedValue([
      { id: 'checking', name: 'Checking', display_name: null, type: 'checking', is_closed: false },
      { id: 'car', name: 'Car loan', display_name: null, type: 'loan', is_closed: false },
      { id: 'card', name: 'Visa', display_name: null, type: 'credit_card', is_closed: false },
    ])
    api.spendingPlan.simulate.mockResolvedValue(timeline())
    api.spendingPlan.preview.mockResolvedValue(timeline())
  })

  it('shows what is left today and whether the daily amount lasts', async () => {
    api.spendingPlan.get.mockResolvedValue(plan)

    renderWithProviders(<SpendingPlanPage />)

    expect(await screen.findByText('$20.00')).toBeInTheDocument()
    expect(screen.getByText('$10.00 spent of $30.00')).toBeInTheDocument()
    expect(
      screen.getByText('At $30.00 a day, the balance stays at or above $200.00 every day until 12/31/26.'),
    ).toBeInTheDocument()
    expect(api.spendingPlan.simulate).toHaveBeenCalledWith([], undefined)
  })

  it('looks up the balance at the end of a picked day and takes it apart', async () => {
    api.spendingPlan.get.mockResolvedValue(plan)

    const { user } = renderWithProviders(<SpendingPlanPage />)
    // The end of this month is picked first.
    expect(await screen.findByTestId('lookup-balance')).toHaveTextContent('$3,380.00')

    await user.click(screen.getByRole('button', { name: 'Next month' }))
    await user.click(screen.getByRole('button', { name: 'Sun, December 20, 2026' }))

    const lookup = within(screen.getByTestId('lookup-balance').closest<HTMLElement>('[data-slot=card]')!)
    expect(screen.getByTestId('lookup-balance')).toHaveTextContent('$1,080.00')
    expect(lookup.getByText('at the end of Sun, December 20, 2026')).toBeInTheDocument()
    const daily = lookup.getByText('Daily spending · 39 days × $30.00').closest('tr')!
    expect(daily).toHaveTextContent('−$1,160.00')
    const bills = lookup.getByText('Bills and planned').closest('tr')!
    expect(bills).toHaveTextContent('−$1,700.00')
    expect(bills).toHaveTextContent('Rent −1.5K · Dentist −200')
  })

  it('counts a what-if only while it is on', async () => {
    window.localStorage.setItem(
      'spendingPlan.whatIf.ws-1',
      JSON.stringify([
        { id: 'tv', label: 'TV', amount: 1200, first_date: '2026-12-01', installments: 3, enabled: true },
      ]),
    )
    api.spendingPlan.get.mockResolvedValue(plan)

    const { user } = renderWithProviders(<SpendingPlanPage />)
    await waitFor(() =>
      expect(api.spendingPlan.simulate).toHaveBeenCalledWith(
        [{ description: 'TV', amount: 1200, installments: 3, first_date: '2026-12-01' }],
        undefined,
      ),
    )

    await user.click(await screen.findByRole('switch', { name: /TV/ }))

    await waitFor(() => expect(api.spendingPlan.simulate).toHaveBeenLastCalledWith([], undefined))
    expect(JSON.parse(window.localStorage.getItem('spendingPlan.whatIf.ws-1')!)[0].enabled).toBe(false)
  })

  it('adds a what-if and sends it', async () => {
    api.spendingPlan.get.mockResolvedValue(plan)

    const { user } = renderWithProviders(<SpendingPlanPage />)
    await user.click(await screen.findByRole('button', { name: 'Add' }))
    await user.type(screen.getByLabelText('What'), 'IPVA')
    await user.type(screen.getByLabelText('Amount'), '3000')
    await user.click(screen.getAllByRole('button', { name: 'Add' }).at(-1)!)

    await waitFor(() =>
      expect(api.spendingPlan.simulate).toHaveBeenLastCalledWith(
        [{ description: 'IPVA', amount: 3000, installments: 1, first_date: expect.any(String) }],
        undefined,
      ),
    )
  })

  it('explains the safe amount with a receipt that adds up to it', async () => {
    api.spendingPlan.get.mockResolvedValue(plan)

    renderWithProviders(<SpendingPlanPage />)

    expect(await screen.findByText('Where the safe $52.56 a day comes from')).toBeInTheDocument()
    expect(screen.getByTestId('receipt-per-day')).toHaveTextContent('$52.56')
    const free = screen.getByText('Free for day-to-day spending').closest('tr')!
    expect(free).toHaveTextContent('$2,050.00')
    expect(screen.getByText('Why until 12/20/26 and not 12/31/26?')).toBeInTheDocument()
    expect(
      screen.getByText(
        `Each $1,000.00 spent before 12/20/26 costs $${(1000 / daysThrough(TODAY, '2026-12-20')).toFixed(2)} a day.`,
      ),
    ).toBeInTheDocument()
  })

  it('switches the month between the calendar and the spreadsheet', async () => {
    api.spendingPlan.get.mockResolvedValue(plan)

    const { user } = renderWithProviders(<SpendingPlanPage />)
    expect(await screen.findByRole('button', { name: 'Fri, November 20, 2026' })).toHaveTextContent('+3K Salary')

    await user.click(screen.getByRole('button', { name: 'Spreadsheet' }))

    const sheet = screen.getByRole('columnheader', { name: 'In' }).closest('table')!
    const salaryDay = within(sheet).getByRole('button', { name: 'Fri, November 20, 2026' }).closest('tr')!
    expect(salaryDay).toHaveTextContent('Salary')
    expect(salaryDay).toHaveTextContent('+$3,000.00')

    await user.click(screen.getByRole('button', { name: 'Calendar' }))
    expect(screen.queryByRole('columnheader', { name: 'In' })).not.toBeInTheDocument()
  })

  it('previews a daily amount from the slider and saves it with the rest of the plan', async () => {
    api.spendingPlan.get.mockResolvedValue({ ...plan, account_ids: ['checking'], horizon_months: 18 })
    api.spendingPlan.save.mockResolvedValue({ ...plan, daily_allowance: '45.00' })

    const { user } = renderWithProviders(<SpendingPlanPage />)
    fireEvent.change(await screen.findByLabelText('Daily amount'), { target: { value: '45' } })

    await waitFor(() => expect(api.spendingPlan.simulate).toHaveBeenLastCalledWith([], 45))
    await user.click(screen.getByRole('button', { name: 'Save $45.00 a day' }))
    expect(api.spendingPlan.save).toHaveBeenCalledWith({
      daily_allowance: 45,
      balance_floor: 200,
      account_ids: ['checking'],
      horizon_months: 18,
    })
  })

  it('saves every field typed in the dialog, not just the last one', async () => {
    api.spendingPlan.get.mockResolvedValue(null)
    api.spendingPlan.save.mockResolvedValue({
      daily_allowance: '45.00',
      balance_floor: '300.00',
      account_ids: null,
      horizon_months: 18,
    })

    const { user } = renderWithProviders(<SpendingPlanPage />)
    await user.click(await screen.findByRole('button', { name: 'Create plan' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Daily amount'), '45')
    await user.type(within(dialog).getByLabelText('Balance floor'), '300')
    await user.selectOptions(within(dialog).getByLabelText('Plan ahead for'), '18')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(api.spendingPlan.save).toHaveBeenCalledWith({
      daily_allowance: 45,
      balance_floor: 300,
      account_ids: null,
      horizon_months: 18,
    })
  })

  it('previews the floor and horizon being edited, not the saved ones', async () => {
    api.spendingPlan.get.mockResolvedValue(null)

    const { user } = renderWithProviders(<SpendingPlanPage />)
    await user.click(await screen.findByRole('button', { name: 'Create plan' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Balance floor'), '300')
    await user.selectOptions(within(dialog).getByLabelText('Plan ahead for'), '18')

    await waitFor(() =>
      expect(api.spendingPlan.preview).toHaveBeenLastCalledWith(
        expect.objectContaining({ balance_floor: 300, horizon_months: 18 }),
      ),
    )
  })

  it('leaves credit cards out of the account checklist because they always count', async () => {
    api.spendingPlan.get.mockResolvedValue(null)

    const { user } = renderWithProviders(<SpendingPlanPage />)
    await user.click(await screen.findByRole('button', { name: 'Create plan' }))
    const dialog = await screen.findByRole('dialog')

    expect(await within(dialog).findByText('Checking')).toBeInTheDocument()
    expect(within(dialog).queryByText('Visa')).not.toBeInTheDocument()
  })

  it('suggests a safe amount before there is a plan', async () => {
    api.spendingPlan.get.mockResolvedValue(null)

    renderWithProviders(<SpendingPlanPage />)

    expect(await screen.findByText("You don't have a daily plan yet.")).toBeInTheDocument()
    expect(api.spendingPlan.simulate).toHaveBeenCalledWith([], undefined)
  })
})
